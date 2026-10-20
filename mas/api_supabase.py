"""API routes for the self-hosted Supabase instance and connecting it to apps."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from mas.db import Database
from mas.deploy import DOMAIN_RE, Deployer
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import Host
from mas.supabase import COMPONENTS, CORE_RAM, SupabaseError, SupabaseManager, estimate_ram, requirements


class InstallBody(BaseModel):
    domain: str | None = Field(default=None, max_length=253)
    components: list[str] | None = Field(default=None, max_length=len(COMPONENTS))


class ComponentsBody(BaseModel):
    components: list[str] = Field(max_length=len(COMPONENTS))


def _check_components(names: list[str] | None) -> list[str] | None:
    unknown = [n for n in names or [] if n not in COMPONENTS]
    if unknown:
        raise HTTPException(400, f"unknown components: {', '.join(unknown)}")
    return names


def register(app: FastAPI, *, host: Host, db: Database, current_session: Callable, deployer: Deployer,
             manager: SupabaseManager) -> None:

    def call(fn, *args):
        try:
            return fn(*args)
        except SupabaseError as exc:
            raise HTTPException(409, str(exc)) from None

    def public(inst: dict | None) -> dict | None:
        if not inst:
            return None
        out = {k: inst[k] for k in ("status", "domain", "ref", "error", "last_backup", "created_at")}
        out["ports"] = manager.ports(inst)
        out["api_url"] = manager.api_url(inst) if out["ports"] else None
        out["components"] = manager.components(inst)
        out["estimated_ram_mb"] = estimate_ram(out["components"])
        return out

    catalog = {"core_ram_mb": CORE_RAM,
               "components": [{"id": k, "label": c["label"], "help": c["help"], "ram_mb": c["ram"],
                               "default": c["default"]} for k, c in COMPONENTS.items()]}

    @app.get("/api/supabase")
    async def status(_=Depends(current_session)) -> dict:
        reqs = await asyncio.to_thread(requirements, host)
        return {"instance": public(manager.get()), "requirements": reqs, "catalog": catalog}

    @app.post("/api/supabase")
    def install(body: InstallBody, s=Depends(current_session)) -> dict:
        domain = (body.domain or "").strip().lower() or None
        if domain and not DOMAIN_RE.fullmatch(domain):
            raise HTTPException(400, f"invalid domain: {domain}")
        call(manager.start_install, domain, _check_components(body.components))
        db.audit(s.username, "supabase-install", True, detail=domain or "no domain")
        return {"ok": True}

    @app.get("/api/supabase/log", response_class=PlainTextResponse)
    def install_log(_=Depends(current_session)) -> str:
        path = manager.log_path()
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    @app.get("/api/supabase/log/stream")
    async def install_stream(_=Depends(current_session)):
        path = manager.log_path()

        async def events():
            pos, partial = 0, ""
            while True:
                chunk = ""
                if path.is_file():
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                if chunk:
                    lines = (partial + chunk).split("\n")
                    partial = lines.pop()
                    for line in lines:
                        yield f"data: {line}\n\n"
                inst = manager.get()
                if (not inst or inst["status"] != "installing") and not chunk:
                    yield f"event: end\ndata: {inst['status'] if inst else 'none'}\n\n"
                    return
                await asyncio.sleep(0.7)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/supabase/credentials")
    def credentials(s=Depends(current_session)) -> dict:
        creds = call(manager.credentials)
        db.audit(s.username, "supabase-credentials", True)
        return creds

    @app.post("/api/supabase/actions/{action}")
    async def action(action: str, s=Depends(current_session)) -> dict:
        ok, output = await asyncio.to_thread(call, manager.action, action)
        db.audit(s.username, f"supabase-{action}", ok, detail=output[-2000:])
        if not ok:
            raise HTTPException(502, output or f"{action} failed")
        return {"ok": True}

    @app.put("/api/supabase/components")
    async def reconfigure(body: ComponentsBody, s=Depends(current_session)) -> dict:
        names = _check_components(body.components) or []
        await asyncio.to_thread(call, manager.reconfigure, names)
        db.audit(s.username, "supabase-components", True, detail=", ".join(names) or "core only")
        return {"ok": True, "components": names, "estimated_ram_mb": estimate_ram(names)}

    @app.post("/api/supabase/studio/{action}")
    async def studio(action: str, s=Depends(current_session)) -> dict:
        await asyncio.to_thread(call, manager.studio, action)
        db.audit(s.username, f"supabase-studio-{action}", True)
        return {"ok": True}

    @app.get("/api/supabase/stats")
    async def stats(_=Depends(current_session)) -> dict:
        return await asyncio.to_thread(manager.stats)

    @app.get("/api/supabase/backups")
    def backups(_=Depends(current_session)) -> list[dict]:
        return manager.list_backups()

    @app.post("/api/supabase/backups")
    async def backup_now(s=Depends(current_session)) -> dict:
        path = await asyncio.to_thread(call, manager.backup)
        db.audit(s.username, "supabase-backup", True, detail=path.name)
        return {"ok": True, "name": path.name}

    @app.delete("/api/supabase")
    async def remove(purge: bool = False, s=Depends(current_session)) -> dict:
        done = await asyncio.to_thread(call, manager.remove, purge)
        db.audit(s.username, "supabase-remove", True, detail="; ".join(done))
        return {"ok": True, "done": done}

    @app.post("/api/apps/{app_id}/supabase")
    def connect(app_id: str, s=Depends(current_session)) -> dict:
        target = db.get_app(app_id)
        if target is None:
            raise HTTPException(404, f"unknown app {app_id}")
        if target["mode"] != "managed":
            raise HTTPException(400, "adopted services manage their own environment: copy the credentials instead")
        variables = call(manager.connection_env)
        fernet = load_fernet(host, deployer.config.secret_key_path)
        env = decrypt_env(fernet, target["env"]) | variables
        db.update_app(app_id, env=encrypt_env(fernet, env))
        db.audit(s.username, "supabase-connect", True, detail=f"{app_id}: {', '.join(sorted(variables))}")
        rid = deployer.enqueue(app_id, "manual", s.username)
        return {"ok": True, "variables": sorted(variables), "release_id": rid, "at": time.time()}
