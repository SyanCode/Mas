"""DNS providers behind one interface: list zones, read and write A/AAAA records.

Each provider only needs to list zones, read the A/AAAA/CNAME records of one
name, create a record and change its target. Mas never deletes records.
Namecheap is not supported: its API needs a whitelisted IP and replaces the
whole zone on every write.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import quote

from mas.ovh import ENDPOINTS as OVH_ENDPOINTS
from mas.ovh import OvhClient, OvhCredentials, OvhError
from mas.ovh import token_url as ovh_token_url

Opener = Callable[[str, str, dict, bytes | None], tuple[int, Any]]
ZONE_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RECORD_TYPES = ("A", "AAAA", "CNAME")
TTL = 300


class DnsError(Exception):
    pass


class DnsConflict(DnsError):
    """The name already points somewhere else (``kind``: points-elsewhere | cname)."""

    def __init__(self, kind: str, message: str, records: list[dict]) -> None:
        super().__init__(message)
        self.kind = kind
        self.records = records


@dataclass
class CredentialField:
    name: str
    label: str
    secret: bool = True
    options: list[str] = field(default_factory=list)


def urllib_opener(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DnsError(f"cannot reach the DNS provider: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def validate_zone(zone: str) -> str:
    zone = zone.strip().lower().rstrip(".")
    if not ZONE_RE.fullmatch(zone):
        raise DnsError(f"invalid domain: {zone}")
    return zone


def validate_subdomain(subdomain: str) -> str:
    sub = subdomain.strip().lower().strip(".")
    if sub and not all(LABEL_RE.fullmatch(label) for label in sub.split(".")):
        raise DnsError("invalid subdomain: letters, digits and dashes, separated by dots")
    return sub


def fqdn(zone: str, subdomain: str) -> str:
    return f"{subdomain}.{zone}" if subdomain else zone


def _message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            if isinstance(payload.get(key), str):
                return payload[key]
        errors = payload.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("message", ""))
    return ""


class Provider:
    """A record is ``{"id", "type", "target", ...}`` for one (zone, subdomain)."""

    type: ClassVar[str]
    label: ClassVar[str]
    fields: ClassVar[list[CredentialField]]
    help: ClassVar[str]
    token_urls: ClassVar[dict[str, str]]

    def __init__(self, creds: dict[str, str], opener: Opener | None = None) -> None:
        missing = [f.name for f in self.fields if not creds.get(f.name)]
        if missing:
            raise DnsError(f"{self.label}: missing {', '.join(missing)}")
        self.creds = creds
        self.opener = opener or urllib_opener

    def _call(self, method: str, url: str, headers: dict, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Accept": "application/json", **headers}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        status, payload = self.opener(method, url, hdrs, data)
        if status >= 400:
            raise DnsError(f"{self.label} API {method} {url.split('?')[0]}: HTTP {status} {_message(payload)}".strip())
        return payload

    def verify(self) -> str:
        """Check the credentials; return a label for the account."""
        raise NotImplementedError

    def zones(self) -> list[str]:
        raise NotImplementedError

    def records(self, zone: str, sub: str) -> list[dict]:
        raise NotImplementedError

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        raise NotImplementedError

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        raise NotImplementedError

    def commit(self, zone: str) -> None:
        """Apply pending changes (only OVH needs it)."""


# -- OVH ---------------------------------------------------------------------------


class OvhProvider(Provider):
    type = "ovh"
    label = "OVHcloud"
    fields = [CredentialField("endpoint", "Region", secret=False, options=list(OVH_ENDPOINTS)),
              CredentialField("application_key", "Application key", secret=False),
              CredentialField("application_secret", "Application secret"),
              CredentialField("consumer_key", "Consumer key")]
    help = "Create a token (rights pre-filled: read zones, create/update records, no delete), validity Unlimited."
    token_urls = {name: ovh_token_url(name) for name in OVH_ENDPOINTS}

    def __init__(self, creds: dict[str, str], opener: Opener | None = None) -> None:
        super().__init__(creds, opener)
        try:
            self.client = OvhClient(OvhCredentials(creds["endpoint"], creds["application_key"],
                                                   creds["application_secret"], creds["consumer_key"]), self.opener)
        except OvhError as exc:
            raise DnsError(str(exc)) from None

    @staticmethod
    def _ovh(fn, *args):
        try:
            return fn(*args)
        except OvhError as exc:
            raise DnsError(str(exc)) from None

    def verify(self) -> str:
        return self._ovh(self.client.me).get("nichandle") or "OVH account"

    def zones(self) -> list[str]:
        return self._ovh(self.client.zones)

    def records(self, zone: str, sub: str) -> list[dict]:
        return [{"id": r["id"], "type": r.get("fieldType"), "target": r.get("target")}
                for r in self._ovh(self.client.records, zone, sub)]

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        self._ovh(self.client.create_record, zone, sub, rtype, target)

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        self._ovh(self.client.update_record, zone, record["id"], target)

    def commit(self, zone: str) -> None:
        self._ovh(self.client.refresh, zone)


# -- Cloudflare ----------------------------------------------------------------------


class CloudflareProvider(Provider):
    type = "cloudflare"
    label = "Cloudflare"
    fields = [CredentialField("api_token", "API token")]
    help = "My Profile → API Tokens → Create Token → template “Edit zone DNS” (all zones or the ones you choose)."
    token_urls = {"default": "https://dash.cloudflare.com/profile/api-tokens"}
    base = "https://api.cloudflare.com/client/v4"

    def _cf(self, method: str, path: str, body: dict | None = None) -> Any:
        payload = self._call(method, self.base + path, {"Authorization": f"Bearer {self.creds['api_token']}"}, body)
        if not isinstance(payload, dict) or not payload.get("success", False):
            raise DnsError(f"Cloudflare: {_message(payload) or 'request failed'}")
        return payload

    def verify(self) -> str:
        self._cf("GET", "/user/tokens/verify")
        return "API token"

    def _zone_ids(self) -> dict[str, str]:
        out, page = {}, 1
        while True:
            data = self._cf("GET", f"/zones?per_page=50&page={page}")
            for z in data.get("result", []):
                out[z["name"]] = z["id"]
            info = data.get("result_info") or {}
            if page >= int(info.get("total_pages", 1) or 1):
                return out
            page += 1

    def zones(self) -> list[str]:
        return sorted(self._zone_ids())

    def _zone_id(self, zone: str) -> str:
        ids = self._zone_ids()
        if zone not in ids:
            raise DnsError(f"{zone} is not a zone of this Cloudflare token")
        return ids[zone]

    def records(self, zone: str, sub: str) -> list[dict]:
        zid, name = self._zone_id(zone), fqdn(zone, sub)
        out = []
        for rtype in RECORD_TYPES:
            data = self._cf("GET", f"/zones/{zid}/dns_records?type={rtype}&name={quote(name)}")
            out += [{"id": r["id"], "type": r["type"], "target": r["content"], "zone_id": zid}
                    for r in data.get("result", [])]
        return out

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        # DNS only (grey cloud): certbot and visitors reach this server directly.
        self._cf("POST", f"/zones/{self._zone_id(zone)}/dns_records",
                 {"type": rtype, "name": fqdn(zone, sub), "content": target, "ttl": 1, "proxied": False})

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        self._cf("PATCH", f"/zones/{record['zone_id']}/dns_records/{record['id']}", {"content": target})


# -- Gandi LiveDNS ---------------------------------------------------------------------


class GandiProvider(Provider):
    type = "gandi"
    label = "Gandi"
    fields = [CredentialField("token", "Personal access token")]
    help = "Organization → Personal Access Tokens → create one with “Manage domain name technical configurations”."
    token_urls = {"default": "https://admin.gandi.net/organizations/account/pat"}
    base = "https://api.gandi.net/v5/livedns"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.creds['token']}"}

    def verify(self) -> str:
        self.zones()
        return "Gandi token"

    def zones(self) -> list[str]:
        return sorted(d["fqdn"] for d in self._call("GET", f"{self.base}/domains", self._auth()) or [])

    def records(self, zone: str, sub: str) -> list[dict]:
        name = sub or "@"
        out = []
        for rtype in RECORD_TYPES:
            status, data = self.opener("GET", f"{self.base}/domains/{zone}/records/{quote(name)}/{rtype}",
                                       {"Accept": "application/json", **self._auth()}, None)
            if status == 404:
                continue
            if status >= 400:
                raise DnsError(f"Gandi API: HTTP {status} {_message(data)}".strip())
            out += [{"id": f"{name}/{rtype}", "type": rtype, "target": v} for v in (data or {}).get("rrset_values", [])]
        return out

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        self._call("POST", f"{self.base}/domains/{zone}/records", self._auth(),
                   {"rrset_name": sub or "@", "rrset_type": rtype, "rrset_values": [target], "rrset_ttl": TTL})

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        self._call("PUT", f"{self.base}/domains/{zone}/records/{quote(sub or '@')}/{record['type']}", self._auth(),
                   {"rrset_values": [target], "rrset_ttl": TTL})


# -- Hetzner DNS ---------------------------------------------------------------------


class HetznerProvider(Provider):
    type = "hetzner"
    label = "Hetzner DNS"
    fields = [CredentialField("api_token", "API token")]
    help = "DNS Console → Manage API tokens → Create access token."
    token_urls = {"default": "https://dns.hetzner.com/settings/api-token"}
    base = "https://dns.hetzner.com/api/v1"

    def _h(self, method: str, path: str, body: dict | None = None) -> Any:
        return self._call(method, self.base + path, {"Auth-API-Token": self.creds["api_token"]}, body)

    def _zone_ids(self) -> dict[str, str]:
        return {z["name"]: z["id"] for z in (self._h("GET", "/zones?per_page=100") or {}).get("zones", [])}

    def verify(self) -> str:
        self._zone_ids()
        return "Hetzner token"

    def zones(self) -> list[str]:
        return sorted(self._zone_ids())

    def records(self, zone: str, sub: str) -> list[dict]:
        zid = self._zone_ids().get(zone)
        if not zid:
            raise DnsError(f"{zone} is not a zone of this Hetzner account")
        name = sub or "@"
        data = self._h("GET", f"/records?zone_id={zid}") or {}
        return [{"id": r["id"], "type": r["type"], "target": r["value"], "zone_id": zid}
                for r in data.get("records", []) if r.get("name") == name and r.get("type") in RECORD_TYPES]

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        self._h("POST", "/records", {"zone_id": self._zone_ids()[zone], "type": rtype, "name": sub or "@",
                                     "value": target, "ttl": TTL})

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        self._h("PUT", f"/records/{record['id']}", {"zone_id": record["zone_id"], "type": record["type"],
                                                    "name": sub or "@", "value": target, "ttl": TTL})


# -- DigitalOcean ----------------------------------------------------------------------


class DigitalOceanProvider(Provider):
    type = "digitalocean"
    label = "DigitalOcean"
    fields = [CredentialField("token", "API token")]
    help = "API → Tokens → Generate New Token with the “domain” read and write scopes."
    token_urls = {"default": "https://cloud.digitalocean.com/account/api/tokens"}
    base = "https://api.digitalocean.com/v2"

    def _do(self, method: str, path: str, body: dict | None = None) -> Any:
        return self._call(method, self.base + path, {"Authorization": f"Bearer {self.creds['token']}"}, body)

    def verify(self) -> str:
        return (self._do("GET", "/account") or {}).get("account", {}).get("email") or "DigitalOcean token"

    def zones(self) -> list[str]:
        return sorted(d["name"] for d in (self._do("GET", "/domains?per_page=200") or {}).get("domains", []))

    def records(self, zone: str, sub: str) -> list[dict]:
        out = []
        for rtype in RECORD_TYPES:
            data = self._do("GET", f"/domains/{zone}/records?type={rtype}&name={quote(fqdn(zone, sub))}") or {}
            out += [{"id": r["id"], "type": r["type"], "target": r["data"]} for r in data.get("domain_records", [])]
        return out

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        self._do("POST", f"/domains/{zone}/records", {"type": rtype, "name": sub or "@", "data": target, "ttl": TTL})

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        self._do("PUT", f"/domains/{zone}/records/{record['id']}", {"data": target})


# -- Porkbun -------------------------------------------------------------------------


class PorkbunProvider(Provider):
    type = "porkbun"
    label = "Porkbun"
    fields = [CredentialField("apikey", "API key", secret=False), CredentialField("secretapikey", "Secret API key")]
    help = "Account → API Access → create a key, then enable “API Access” on each domain."
    token_urls = {"default": "https://porkbun.com/account/api"}
    base = "https://api.porkbun.com/api/json/v3"

    def _p(self, path: str, body: dict | None = None) -> Any:
        payload = self._call("POST", self.base + path, {},
                             {"apikey": self.creds["apikey"], "secretapikey": self.creds["secretapikey"],
                              **(body or {})})
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            raise DnsError(f"Porkbun: {_message(payload) or 'request failed'}")
        return payload

    def verify(self) -> str:
        self._p("/ping")
        return "Porkbun API key"

    def zones(self) -> list[str]:
        return sorted(d["domain"] for d in self._p("/domain/listAll").get("domains", []))

    def records(self, zone: str, sub: str) -> list[dict]:
        out = []
        for rtype in RECORD_TYPES:
            data = self._p(f"/dns/retrieveByNameType/{zone}/{rtype}/{quote(sub)}")
            out += [{"id": r["id"], "type": r["type"], "target": r["content"]} for r in data.get("records", [])]
        return out

    def create(self, zone: str, sub: str, rtype: str, target: str) -> None:
        self._p(f"/dns/create/{zone}", {"name": sub, "type": rtype, "content": target, "ttl": "600"})

    def update(self, zone: str, sub: str, record: dict, target: str) -> None:
        self._p(f"/dns/edit/{zone}/{record['id']}", {"name": sub, "type": record["type"], "content": target,
                                                    "ttl": "600"})


PROVIDERS: dict[str, type[Provider]] = {
    cls.type: cls for cls in (OvhProvider, CloudflareProvider, GandiProvider, HetznerProvider,
                              DigitalOceanProvider, PorkbunProvider)
}


def catalog() -> list[dict]:
    return [{"type": cls.type, "label": cls.label, "help": cls.help, "token_urls": cls.token_urls,
             "fields": [{"name": f.name, "label": f.label, "secret": f.secret, "options": f.options}
                        for f in cls.fields]} for cls in PROVIDERS.values()]


def make_provider(ptype: str, creds: dict[str, str], opener: Opener | None = None) -> Provider:
    if ptype not in PROVIDERS:
        raise DnsError(f"unknown DNS provider {ptype}")
    return PROVIDERS[ptype](creds, opener)


def point_domain(provider: Provider, zone: str, subdomain: str, ipv4: str, ipv6: str | None = None,
                 overwrite: bool = False) -> dict:
    """Make ``subdomain.zone`` point to this server (A, and AAAA if ``ipv6``).

    Records pointing elsewhere are only replaced with ``overwrite``; a CNAME on
    that name is never touched; nothing is ever deleted.
    """
    zone = validate_zone(zone)
    if zone not in provider.zones():
        raise DnsError(f"{zone} is not a zone of this {provider.label} account")
    sub = validate_subdomain(subdomain)
    name = fqdn(zone, sub)
    existing = provider.records(zone, sub)
    cnames = [r for r in existing if r["type"] == "CNAME"]
    if cnames:
        raise DnsConflict("cname", f"{name} is a CNAME to {cnames[0]['target']}; change it at {provider.label}",
                          cnames)
    wanted = {"A": ipv4, **({"AAAA": ipv6} if ipv6 else {})}
    elsewhere = [r for r in existing if r["type"] in wanted and r["target"] != wanted[r["type"]]]
    if elsewhere and not overwrite:
        targets = ", ".join(f"{r['type']} {r['target']}" for r in elsewhere)
        raise DnsConflict("points-elsewhere", f"{name} already points to {targets}", elsewhere)

    changes = []
    for rtype, target in wanted.items():
        same = [r for r in existing if r["type"] == rtype]
        if any(r["target"] == target for r in same):
            changes.append(f"{rtype} {target} (already set)")
        elif same:
            provider.update(zone, sub, same[0], target)
            changes.append(f"{rtype} {same[0]['target']} → {target}")
        else:
            provider.create(zone, sub, rtype, target)
            changes.append(f"{rtype} {target} (created)")
    if any("already set" not in c for c in changes):
        provider.commit(zone)
    return {"domain": name, "changes": changes, "provider": provider.label}
