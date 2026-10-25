"""API routes of the maintenance mode: pages (written by Mas or imported) and sites put on hold."""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mas.db import Database
from mas.deploy import DeployError
from mas.maintenance import Maintenance, MaintenanceError

MAX_UPLOAD = 15 * 1024 * 1024


class SimplePageBody(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    name: str = Field(default="", max_length=60)
    title: str = Field(max_length=120)
    message: str = Field(default="", max_length=2000)
    back: str = Field(default="", max_length=200)
    accent: str = Field(default="#6366f1", max_length=9)


class MaintenanceBody(BaseModel):
    page: str = Field(default="default", max_length=40)
    allow: list[str] = Field(default_factory=list, max_length=50)
    until: str | None = Field(default=None, max_length=40)


def register(app: FastAPI, *, db: Database, current_session: Callable, maintenance: Maintenance,
             client_ip: Callable[[Request], str]) -> None:

    def call(fn, *args):
        try:
            return fn(*args)
        except MaintenanceError as exc:
            raise HTTPException(400, str(exc)) from None
        except DeployError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/maintenance")
    def overview(request: Request, _=Depends(current_session)) -> dict:
        return {**maintenance.overview(), "your_ip": client_ip(request)}

    @app.post("/api/maintenance/pages")
    async def save_page(body: SimplePageBody, s=Depends(current_session)) -> dict:
        page_id = await asyncio.to_thread(call, maintenance.save_simple, body.id, body.name, body.title, body.message,
                                          body.back, body.accent)
        db.audit(s.username, "maintenance-page", True, detail=page_id)
        return {"id": page_id}

    @app.post("/api/maintenance/pages/import")
    async def import_page(request: Request, name: str = Query("page", max_length=60),
                          page_id: str | None = Query(None, alias="id", max_length=40), s=Depends(current_session)) -> dict:
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large")
        page = await asyncio.to_thread(call, maintenance.import_page, name, data, page_id)
        db.audit(s.username, "maintenance-page", True, detail=f"{page} (imported)")
        return {"id": page}

    @app.delete("/api/maintenance/pages/{page_id}")
    def delete_page(page_id: str, s=Depends(current_session)) -> dict:
        call(maintenance.delete_page, page_id)
        db.audit(s.username, "maintenance-page-delete", True, detail=page_id)
        return {"ok": True}

    @app.get("/api/maintenance/pages/{page_id}/files/{path:path}")
    def page_file(page_id: str, path: str) -> Response:
        """The page as nginx serves it, for the preview of the dashboard (same files, same base). No session:
        these files are public (every visitor of a site in maintenance gets them), and the preview is a
        sandboxed iframe whose requests carry no cookie."""
        call(maintenance.ensure_page, page_id)
        data = call(maintenance.page_file, page_id, path)
        if data is None:
            raise HTTPException(404, "no such file")
        mime = mimetypes.guess_type(path or "index.html")[0] or "application/octet-stream"
        if (path or "index.html") == "index.html":
            data = data.replace(b'<base href="/__mas_maintenance/">',
                                f'<base href="/api/maintenance/pages/{page_id}/files/">'.encode(), 1)
        return Response(data, media_type=mime, headers={"Content-Security-Policy": "script-src 'none'; sandbox"})

    @app.put("/api/apps/{app_id}/maintenance")
    async def enable(app_id: str, body: MaintenanceBody, s=Depends(current_session)) -> dict:
        state = await asyncio.to_thread(call, maintenance.enable, app_id, body.page, body.allow, body.until, s.username)
        db.audit(s.username, "maintenance-on", True, service_id=app_id,
                 detail=f"page {body.page}; allowed: {', '.join(state['allow']) or 'nobody'}"
                        + (f"; until {state['until']}" if state["until"] else ""))
        return state

    @app.delete("/api/apps/{app_id}/maintenance")
    async def disable(app_id: str, s=Depends(current_session)) -> dict:
        await asyncio.to_thread(call, maintenance.disable, app_id)
        db.audit(s.username, "maintenance-off", True, service_id=app_id)
        return {"ok": True}
