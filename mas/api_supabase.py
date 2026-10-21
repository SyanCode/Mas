"""API routes for the self-hosted Supabase instance and connecting it to apps."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from mas.appdb import AppDatabases, AppDbError
from mas.db import Database
from mas.deploy import DOMAIN_RE, Deployer
from mas.host import Host
from mas.supabase import COMPONENTS, CORE_RAM, PRESETS, SupabaseError, SupabaseManager, estimate_ram, requirements


class InstallBody(BaseModel):
    domain: str | None = Field(default=None, max_length=253)
    components: list[str] | None = Field(default=None, max_length=len(COMPONENTS))
    connect: list[str] = Field(default_factory=list, max_length=100)  # apps connected once it runs
    seed: bool = False


class ConnectBody(BaseModel):
    seed: bool = False


class ImportBody(BaseModel):
    url: str = Field(min_length=12, max_length=2000)
    with_data: bool = True
    replace: bool = False


class ComponentsBody(BaseModel):
    components: list[str] = Field(max_length=len(COMPONENTS))


def _check_components(names: list[str] | None) -> list[str] | None:
    unknown = [n for n in names or [] if n not in COMPONENTS]
    if unknown:
        raise HTTPException(400, f"unknown components: {', '.join(unknown)}")
    return names


def register(app: FastAPI, *, host: Host, db: Database, current_session: Callable, deployer: Deployer,
             manager: SupabaseManager, appdb: AppDatabases) -> None:

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SupabaseError as exc:
            raise HTTPException(409, str(exc)) from None
        except AppDbError as exc:
            raise HTTPException(400, str(exc)) from None

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
                               "default": c["default"]} for k, c in COMPONENTS.items()],
               "presets": [{"id": k, "label": p["label"], "help": p["help"], "components": p["components"],
                            "ram_mb": estimate_ram(p["components"])} for k, p in PRESETS.items()]}

    @app.get("/api/supabase")
    async def status(_=Depends(current_session)) -> dict:
        reqs = await asyncio.to_thread(requirements, host)
        return {"instance": public(manager.get()), "requirements": reqs, "catalog": catalog}

    @app.post("/api/supabase")
    def install(body: InstallBody, s=Depends(current_session)) -> dict:
        domain = (body.domain or "").strip().lower() or None
        if domain and not DOMAIN_RE.fullmatch(domain):
            raise HTTPException(400, f"invalid domain: {domain}")
        if body.connect:
            call(appdb.queue, body.connect, seed=body.seed)  # checked before anything starts
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

    def known(app_id: str) -> None:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")

    @app.get("/api/apps/{app_id}/database")
    async def database(app_id: str, _=Depends(current_session)) -> dict:
        """What the repository needs, whether the app is connected, its migrations."""
        known(app_id)
        return await asyncio.to_thread(call, appdb.info, app_id)

    @app.post("/api/apps/{app_id}/supabase")
    async def connect(app_id: str, body: ConnectBody | None = None, s=Depends(current_session)) -> dict:
        known(app_id)
        result = await asyncio.to_thread(call, appdb.connect, app_id, s.username, seed=bool(body and body.seed))
        return {"ok": True, **result, "at": time.time()}

    @app.post("/api/apps/{app_id}/database/import")
    async def import_dev(app_id: str, body: ImportBody, s=Depends(current_session)) -> dict:
        known(app_id)
        try:
            return await asyncio.to_thread(call, appdb.import_dev, app_id, body.url, body.with_data, body.replace,
                                           s.username)
        except HTTPException as exc:
            db.audit(s.username, "supabase-import", False, service_id=app_id, detail=str(exc.detail)[:300])
            raise

    @app.get("/api/github/database")
    async def repo_database(repo: str, installation_id: int, ref: str, root_dir: str = "",
                            _=Depends(current_session)) -> dict:
        """Detection before the app exists (new app page)."""
        found = await asyncio.to_thread(appdb.scan, {"id": repo, "repo": repo, "installation_id": installation_id,
                                                     "branch": ref, "root_dir": root_dir})
        inst = manager.get()
        return {"instance": inst["status"] if inst else None,
                **{k: found[k] for k in ("uses_supabase", "evidence", "migrations", "seed")}}
