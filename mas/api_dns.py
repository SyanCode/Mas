"""API routes for DNS: connect OVH, choose this server's IPs, link a domain to an app."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from mas.config import Config
from mas.db import Database
from mas.deploy import DOMAIN_RE, Deployer, domain_conflict, vhost_paths
from mas.dns import IPV4_KEY, IPV6_KEY, load_ovh_credentials, ovh_account, ovh_client, save_ovh, server_ips
from mas.health import guess_public_ip
from mas.host import Host
from mas.ovh import (
    ENDPOINTS, DnsConflict, Opener, OvhClient, OvhCredentials, OvhError, fqdn, point_domain, token_url,
    validate_ip, validate_subdomain,
)


class ConnectOvh(BaseModel):
    endpoint: str = Field(default="ovh-eu", max_length=20)
    application_key: str = Field(max_length=100)
    application_secret: str = Field(max_length=100)
    consumer_key: str = Field(max_length=100)


class DnsSettings(BaseModel):
    ipv4: str | None = Field(default=None, max_length=64)
    ipv6: str | None = Field(default=None, max_length=64)


class RecordBody(BaseModel):
    zone: str = Field(max_length=253)
    subdomain: str = Field(default="", max_length=200)
    overwrite: bool = False


class LinkDomain(BaseModel):
    zone: str | None = Field(default=None, max_length=253)  # OVH zone; None = manual domain
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
    def client() -> OvhClient:
        c = ovh_client(host, config, db, opener)
        if c is None:
            raise HTTPException(409, "connect an OVH account first (Settings → Domains)")
        return c

    def ips() -> tuple[str, str | None]:
        ipv4, ipv6 = server_ips(db, config)
        if not ipv4:
            raise HTTPException(409, "set this server's public IPv4 first (Settings → Domains)")
        return ipv4, ipv6

    def ovh_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DnsConflict as exc:
            raise HTTPException(409, f"{exc.kind}: {exc}") from None
        except OvhError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.get("/api/dns/status")
    async def dns_status(_=Depends(current_session)) -> dict:
        ipv4, ipv6 = server_ips(db, config)
        creds = load_ovh_credentials(host, config, db)
        out = {"provider": "ovh" if creds else None, "endpoint": creds.endpoint if creds else None,
               "account": ovh_account(db), "ipv4": ipv4, "ipv6": ipv6, "suggested_ipv4": None,
               "token_urls": {name: token_url(name) for name in ENDPOINTS}}
        if not ipv4:
            out["suggested_ipv4"] = await asyncio.to_thread(guess_public_ip, host)
        return out

    @app.post("/api/dns/ovh")
    async def connect_ovh(body: ConnectOvh, s=Depends(current_session)) -> dict:
        if body.endpoint not in ENDPOINTS:
            raise HTTPException(400, f"endpoint must be one of {', '.join(ENDPOINTS)}")
        creds = OvhCredentials(body.endpoint, body.application_key.strip(), body.application_secret.strip(),
                               body.consumer_key.strip())
        test = OvhClient(creds, opener) if opener else OvhClient(creds)
        me = await asyncio.to_thread(ovh_call, test.me)
        zones = await asyncio.to_thread(ovh_call, test.zones)
        save_ovh(host, config, db, creds)
        account = {"nichandle": me.get("nichandle"), "name": " ".join(
            x for x in (me.get("firstname"), me.get("name")) if x) or None}
        db.set_setting("ovh_account", json.dumps(account))
        db.audit(s.username, "dns-connect", True, detail=f"OVH {account['nichandle']} ({len(zones)} zone(s))")
        return {"ok": True, "account": account, "zones": zones}

    @app.delete("/api/dns/ovh")
    def disconnect_ovh(s=Depends(current_session)) -> dict:
        save_ovh(host, config, db, None)
        db.set_setting("ovh_account", None)
        db.audit(s.username, "dns-disconnect", True)
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
    async def dns_zones(_=Depends(current_session)) -> list[str]:
        return await asyncio.to_thread(ovh_call, client().zones)

    @app.get("/api/dns/check")
    async def dns_check(zone: str, subdomain: str = "", _=Depends(current_session)) -> dict:
        try:
            sub = validate_subdomain(subdomain)
        except OvhError as exc:
            raise HTTPException(400, str(exc)) from None
        records = await asyncio.to_thread(ovh_call, client().records, zone, sub)
        name = fqdn(zone, sub)
        ipv4, ipv6 = server_ips(db, config)
        here = {ipv4, ipv6} - {None}
        used_by_app = next((a["id"] for a in db.list_apps() if a["domain"] == name), None)
        conflict = await asyncio.to_thread(domain_conflict, host, name, set())
        return {
            "domain": name,
            "records": [{"type": r.get("fieldType"), "target": r.get("target")} for r in records],
            "points_here": any(r.get("target") in here for r in records),
            "points_elsewhere": any(r.get("fieldType") in ("A", "AAAA") and r.get("target") not in here
                                    for r in records),
            "cname": any(r.get("fieldType") == "CNAME" for r in records),
            "used_by": used_by_app or conflict,
        }

    @app.post("/api/dns/records")
    async def create_records(body: RecordBody, s=Depends(current_session)) -> dict:
        ipv4, ipv6 = ips()
        result = await asyncio.to_thread(ovh_call, point_domain, client(), body.zone, body.subdomain, ipv4, ipv6,
                                         body.overwrite)
        db.audit(s.username, "dns-record", True, detail=f"{result['domain']}: {'; '.join(result['changes'])}")
        return result

    @app.post("/api/apps/{app_id}/domain")
    async def link_domain(app_id: str, body: LinkDomain, s=Depends(current_session)) -> dict:
        target = db.get_app(app_id)
        if target is None:
            raise HTTPException(404, f"unknown app {app_id}")
        if target["mode"] != "managed":
            raise HTTPException(400, "an adopted service keeps its own nginx vhost and domain")
        if body.zone:
            try:
                name = fqdn(body.zone.lower(), validate_subdomain(body.subdomain))
            except OvhError as exc:
                raise HTTPException(400, str(exc)) from None
        else:
            name = (body.domain or "").strip().lower()
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
            ipv4, ipv6 = ips()
            result = await asyncio.to_thread(ovh_call, point_domain, client(), body.zone.lower(), body.subdomain,
                                             ipv4, ipv6, body.overwrite)
            changes = result["changes"]
        db.update_app(app_id, domain=name)
        db.audit(s.username, "app-domain", True, detail=f"{app_id} → {name}" + (f" ({'; '.join(changes)})"
                                                                                 if changes else ""))
        rid = deployer.enqueue(app_id, "manual", s.username)
        return {"domain": name, "changes": changes, "release_id": rid}
