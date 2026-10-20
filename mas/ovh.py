"""OVHcloud DNS: list zones, point a (sub)domain to this server.

Uses the official API with signed requests (application key + secret +
consumer key). The token only needs read/create/update on /domain/zone,
never delete.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

ENDPOINTS = {
    "ovh-eu": "https://eu.api.ovh.com/1.0",
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
}
TOKEN_PAGES = {
    "ovh-eu": "https://eu.api.ovh.com/createToken/",
    "ovh-ca": "https://ca.api.ovh.com/createToken/",
    "ovh-us": "https://api.us.ovhcloud.com/createToken/",
}
RIGHTS = [("GET", "/me"), ("GET", "/domain/zone"), ("GET", "/domain/zone/*"), ("POST", "/domain/zone/*"),
          ("PUT", "/domain/zone/*")]
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RECORD_TTL = 300

# (method, url, headers, body) -> (status, parsed JSON or None)
Opener = Callable[[str, str, dict, bytes | None], tuple[int, Any]]


class OvhError(Exception):
    pass


class DnsConflict(OvhError):
    """The name already points somewhere else (``records`` describes what)."""

    def __init__(self, kind: str, message: str, records: list[dict]) -> None:
        super().__init__(message)
        self.kind = kind  # "points-elsewhere" (can be overwritten) | "cname" (never touched)
        self.records = records


def token_url(endpoint: str) -> str:
    query = "&".join(f"{method}={quote(path, safe='/*')}" for method, path in RIGHTS)
    return f"{TOKEN_PAGES[endpoint]}?{query}"


def signature(app_secret: str, consumer_key: str, method: str, url: str, body: str, timestamp: int) -> str:
    raw = "+".join([app_secret, consumer_key, method, url, body, str(timestamp)])
    return "$1$" + hashlib.sha1(raw.encode()).hexdigest()


def _urllib_opener(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OvhError(f"cannot reach OVH: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def fqdn(zone: str, subdomain: str) -> str:
    return f"{subdomain}.{zone}" if subdomain else zone


def validate_subdomain(subdomain: str) -> str:
    sub = subdomain.strip().lower().strip(".")
    if sub and not all(LABEL_RE.fullmatch(label) for label in sub.split(".")):
        raise OvhError("invalid subdomain: letters, digits and dashes, separated by dots")
    return sub


def validate_ip(value: str | None, version: int) -> str | None:
    if not value:
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        raise OvhError(f"invalid IPv{version} address: {value}") from None
    if ip.version != version:
        raise OvhError(f"{value} is not an IPv{version} address")
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise OvhError(f"{value} is not a public address")
    return str(ip)


@dataclass
class OvhCredentials:
    endpoint: str
    application_key: str
    application_secret: str
    consumer_key: str


class OvhClient:
    def __init__(self, creds: OvhCredentials, opener: Opener = _urllib_opener) -> None:
        if creds.endpoint not in ENDPOINTS:
            raise OvhError(f"unknown OVH endpoint {creds.endpoint}")
        self.creds = creds
        self.base = ENDPOINTS[creds.endpoint]
        self.opener = opener
        self._delta: int | None = None

    def _time(self) -> int:
        if self._delta is None:
            status, server = self.opener("GET", f"{self.base}/auth/time", {"Accept": "application/json"}, None)
            self._delta = int(server) - int(time.time()) if status == 200 and server else 0
        return int(time.time()) + self._delta

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = self.base + path
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        ts = self._time()
        headers = {
            "Accept": "application/json",
            "X-Ovh-Application": self.creds.application_key,
            "X-Ovh-Consumer": self.creds.consumer_key,
            "X-Ovh-Timestamp": str(ts),
            "X-Ovh-Signature": signature(self.creds.application_secret, self.creds.consumer_key, method, url,
                                         payload, ts),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, data = self.opener(method, url, headers, payload.encode() if body is not None else None)
        if status >= 400:
            message = data.get("message") if isinstance(data, dict) else None
            raise OvhError(f"OVH API {method} {path}: HTTP {status} {message or ''}".strip())
        return data

    # -- account & zones -------------------------------------------------

    def me(self) -> dict:
        return self.request("GET", "/me")

    def zones(self) -> list[str]:
        return sorted(self.request("GET", "/domain/zone") or [])

    def records(self, zone: str, subdomain: str, field_types: tuple[str, ...] = ("A", "AAAA", "CNAME")) -> list[dict]:
        out = []
        for ftype in field_types:
            ids = self.request("GET", f"/domain/zone/{zone}/record?fieldType={ftype}&subDomain={quote(subdomain)}")
            for rid in ids or []:
                out.append(self.request("GET", f"/domain/zone/{zone}/record/{rid}"))
        return out

    def create_record(self, zone: str, subdomain: str, field_type: str, target: str) -> dict:
        return self.request("POST", f"/domain/zone/{zone}/record",
                            {"fieldType": field_type, "subDomain": subdomain, "target": target, "ttl": RECORD_TTL})

    def update_record(self, zone: str, record_id: int, target: str) -> None:
        self.request("PUT", f"/domain/zone/{zone}/record/{record_id}", {"target": target, "ttl": RECORD_TTL})

    def refresh(self, zone: str) -> None:
        self.request("POST", f"/domain/zone/{zone}/refresh")


def point_domain(client: OvhClient, zone: str, subdomain: str, ipv4: str, ipv6: str | None = None,
                 overwrite: bool = False) -> dict:
    """Make ``subdomain.zone`` point to this server (A, and AAAA if ``ipv6``).

    Existing records pointing elsewhere are only replaced with ``overwrite``;
    a CNAME on that name is never touched.
    """
    if zone not in client.zones():
        raise OvhError(f"{zone} is not a DNS zone of this OVH account")
    sub = validate_subdomain(subdomain)
    name = fqdn(zone, sub)
    existing = client.records(zone, sub)
    cnames = [r for r in existing if r.get("fieldType") == "CNAME"]
    if cnames:
        raise DnsConflict("cname", f"{name} is a CNAME to {cnames[0].get('target')}; change it in the OVH manager",
                          cnames)

    wanted = {"A": ipv4, **({"AAAA": ipv6} if ipv6 else {})}
    elsewhere = [r for r in existing if r.get("fieldType") in wanted and r.get("target") != wanted[r["fieldType"]]]
    if elsewhere and not overwrite:
        targets = ", ".join(f"{r['fieldType']} {r.get('target')}" for r in elsewhere)
        raise DnsConflict("points-elsewhere", f"{name} already points to {targets}", elsewhere)

    changes = []
    for ftype, target in wanted.items():
        same_type = [r for r in existing if r.get("fieldType") == ftype]
        if any(r.get("target") == target for r in same_type):
            changes.append(f"{ftype} {target} (already set)")
            continue
        if same_type:
            client.update_record(zone, same_type[0]["id"], target)
            changes.append(f"{ftype} {same_type[0].get('target')} → {target}")
        else:
            client.create_record(zone, sub, ftype, target)
            changes.append(f"{ftype} {target} (created)")
    if any("already set" not in c for c in changes):
        client.refresh(zone)
    return {"domain": name, "changes": changes}
