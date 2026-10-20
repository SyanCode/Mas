"""OVHcloud DNS: list zones, point a (sub)domain to this server.

Uses the official API with signed requests (application key + secret +
consumer key). The token only needs read/create/update on /domain/zone,
never delete.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
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
RECORD_TTL = 300

# (method, url, headers, body) -> (status, parsed JSON or None)
Opener = Callable[[str, str, dict, bytes | None], tuple[int, Any]]


class OvhError(Exception):
    pass



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

