"""API routes for DNS: connect registrars, choose this server's IPs, link a domain to an app."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from mas.config import Config
from mas.db import Database
from mas.deploy import DOMAIN_RE, Deployer, domain_conflict, vhost_paths
from mas.dns import IPV4_KEY, IPV6_KEY, load_providers, save_providers, server_ips, zone_index
from mas.domains import aliases_for, point_www
from mas.dnsproviders import (
    PROVIDERS, DnsConflict, DnsError, Opener, Provider, catalog, fqdn, make_provider, point_domain, validate_subdomain,
    validate_zone,
)
from mas.health import guess_public_ip
from mas.host import Host
from mas.ovh import OvhError, validate_ip

ZONES_TTL = 60


class ConnectProvider(BaseModel):
    type: str = Field(max_length=30)
    credentials: dict[str, str]


class DnsSettings(BaseModel):
    ipv4: str | None = Field(default=None, max_length=64)
    ipv6: str | None = Field(default=None, max_length=64)


class RecordBody(BaseModel):
    zone: str = Field(max_length=253)
    subdomain: str = Field(default="", max_length=200)
    overwrite: bool = False


class LinkDomain(BaseModel):
    zone: str | None = Field(default=None, max_length=253)  # a connected zone; None = manual domain
    subdomain: str = Field(default="", max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    overwrite: bool = False


def register(
    app: FastAPI,
    *,
    host: Host,
    config: Config,
    db: Database,
    current_session: Callable,
    deployer: Deployer,
    opener: Opener | None = None,
) -> None:
    cache: dict = {}

    def zones() -> dict[str, Provider]:
        if cache.get("at", 0) + ZONES_TTL < time.time():
            index, errors = zone_index(host, config, db, opener)
            cache.update(at=time.time(), index=index, errors=errors)
        return cache["index"]

    def provider_for(zone: str) -> tuple[str, Provider]:
        try:
            zone = validate_zone(zone)
        except DnsError as exc:
            raise HTTPException(400, str(exc)) from None
        index = zones()
        if zone not in index:
            raise HTTPException(404, f"{zone} is not in a connected DNS account")
        return zone, index[zone]

    def ips() -> tuple[str, str | None]:
        ipv4, ipv6 = server_ips(db, config)
        if not ipv4:
            raise HTTPException(409, "set this server's public IPv4 first (Settings → Domains)")
        return ipv4, ipv6

    def dns_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DnsConflict as exc:
            raise HTTPException(409, f"{exc.kind}: {exc}") from None
        except DnsError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.get("/api/dns/status")
    async def dns_status(_=Depends(current_session)) -> dict:
        ipv4, ipv6 = server_ips(db, config)
        connected = load_providers(host, config, db)
        out = {
            "providers": [{"type": t, "label": PROVIDERS[t].label, "account": e.get("account")}
                          for t, e in connected.items() if t in PROVIDERS],
            "catalog": catalog(), "ipv4": ipv4, "ipv6": ipv6, "suggested_ipv4": None,
        }
        if not ipv4:
            out["suggested_ipv4"] = await asyncio.to_thread(guess_public_ip, host)
        return out

    @app.post("/api/dns/providers")
    async def connect_provider(body: ConnectProvider, s=Depends(current_session)) -> dict:
        if body.type not in PROVIDERS:
            raise HTTPException(400, f"unknown provider; supported: {', '.join(PROVIDERS)}")
        allowed = {f.name for f in PROVIDERS[body.type].fields}
        creds = {k: v.strip() for k, v in body.credentials.items() if k in allowed and len(v) <= 500}
        try:
            provider = make_provider(body.type, creds, opener)
        except DnsError as exc:
            raise HTTPException(400, str(exc)) from None
        account = await asyncio.to_thread(dns_call, provider.verify)
        found = await asyncio.to_thread(dns_call, provider.zones)
        connected = load_providers(host, config, db)
        connected[body.type] = {"creds": creds, "account": account}
        save_providers(host, config, db, connected)
        cache.clear()
        db.audit(s.username, "dns-connect", True, detail=f"{provider.label} {account} ({len(found)} zone(s))")
        return {"ok": True, "account": account, "zones": found}

    @app.delete("/api/dns/providers/{ptype}")
    def disconnect_provider(ptype: str, s=Depends(current_session)) -> dict:
        connected = load_providers(host, config, db)
        if connected.pop(ptype, None) is None:
            raise HTTPException(404, "not connected")
        save_providers(host, config, db, connected)
        cache.clear()
        db.audit(s.username, "dns-disconnect", True, detail=ptype)
        return {"ok": True}

    @app.put("/api/dns/settings")
    def dns_settings(body: DnsSettings, s=Depends(current_session)) -> dict:
        try:
            ipv4, ipv6 = validate_ip(body.ipv4, 4), validate_ip(body.ipv6, 6)
        except OvhError as exc:
            raise HTTPException(400, str(exc)) from None
        db.set_setting(IPV4_KEY, ipv4)
        db.set_setting(IPV6_KEY, ipv6)
        db.audit(s.username, "dns-settings", True, detail=f"ipv4={ipv4} ipv6={ipv6}")
        return {"ipv4": ipv4, "ipv6": ipv6}

    @app.get("/api/dns/zones")
    async def dns_zones(_=Depends(current_session)) -> list[dict]:
        index = await asyncio.to_thread(zones)
        return [{"zone": z, "provider": p.type, "label": p.label} for z, p in sorted(index.items())]

    @app.get("/api/dns/check")
    async def dns_check(zone: str, subdomain: str = "", _=Depends(current_session)) -> dict:
        zone, provider = await asyncio.to_thread(provider_for, zone)
        try:
            sub = validate_subdomain(subdomain)
        except DnsError as exc:
            raise HTTPException(400, str(exc)) from None
        records = await asyncio.to_thread(dns_call, provider.records, zone, sub)
        name = fqdn(zone, sub)
        here = set(server_ips(db, config)) - {None}
        used_by_app = next((a["id"] for a in db.list_apps() if a["domain"] == name), None)
        conflict = await asyncio.to_thread(domain_conflict, host, name, set())
        return {
            "domain": name,
            "provider": provider.label,
            "records": [{"type": r["type"], "target": r["target"]} for r in records],
            "points_here": any(r["target"] in here for r in records),
            "points_elsewhere": any(r["type"] in ("A", "AAAA") and r["target"] not in here for r in records),
            "cname": any(r["type"] == "CNAME" for r in records),
            "used_by": used_by_app or conflict,
        }

    async def create_records(zone: str, subdomain: str, overwrite: bool) -> dict:
        zone, provider = await asyncio.to_thread(provider_for, zone)
        ipv4, ipv6 = ips()
        result = await asyncio.to_thread(dns_call, point_domain, provider, zone, subdomain, ipv4, ipv6, overwrite)
        # A root domain brings its www (never overwritten: a www pointing elsewhere is reported, not changed).
        result["changes"] += await asyncio.to_thread(point_www, provider, zone, subdomain.strip().strip("."),
                                                     ipv4, ipv6)
        return result

    @app.post("/api/dns/records")
    async def records(body: RecordBody, s=Depends(current_session)) -> dict:
        result = await create_records(body.zone, body.subdomain, body.overwrite)
        db.audit(s.username, "dns-record", True,
                 detail=f"{result['domain']} ({result['provider']}): {'; '.join(result['changes'])}")
        return result

    @app.post("/api/apps/{app_id}/domain")
    async def link_domain(app_id: str, body: LinkDomain, s=Depends(current_session)) -> dict:
        target = db.get_app(app_id)
        if target is None:
            raise HTTPException(404, f"unknown app {app_id}")
        if target["mode"] != "managed":
            raise HTTPException(400, "an adopted service keeps its own nginx vhost and domain")
        try:
            name = fqdn(validate_zone(body.zone), validate_subdomain(body.subdomain)) if body.zone \
                else (body.domain or "").strip().lower()
        except DnsError as exc:
            raise HTTPException(400, str(exc)) from None
        if not DOMAIN_RE.fullmatch(name):
            raise HTTPException(400, f"invalid domain: {name or '(empty)'}")
        other = next((a["id"] for a in db.list_apps() if a["domain"] == name and a["id"] != app_id), None)
        if other:
            raise HTTPException(409, f"{name} is already used by app {other}")
        conflict = await asyncio.to_thread(domain_conflict, host, name, set(vhost_paths(app_id)))
        if conflict:
            raise HTTPException(409, f"{name} is already served by {conflict}")

        changes: list[str] = []
        if body.zone:
            changes = (await create_records(body.zone, body.subdomain, body.overwrite))["changes"]
        aliases = aliases_for(db, app_id, name, json.loads(target.get("aliases") or "[]"), old_domain=target["domain"])
        db.update_app(app_id, domain=name, aliases=json.dumps(aliases) if aliases else None)
        db.audit(s.username, "app-domain", True,
                 detail=f"{app_id} → {name}" + (f" ({'; '.join(changes)})" if changes else ""))
        rid = deployer.enqueue(app_id, "manual", s.username)
        return {"domain": name, "changes": changes, "release_id": rid}
