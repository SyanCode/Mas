"""API routes of the ready-made services (mail server, Minecraft…): catalog, creation, controls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from mas.db import Database
from mas.stacks import TEMPLATES, StackError, StackManager


class CreateStack(BaseModel):
    template: str = Field(max_length=40)
    id: str = Field(max_length=24)
    domain: str | None = Field(default=None, max_length=253)
    options: dict = Field(default_factory=dict)
    ports: dict[str, int] = Field(default_factory=dict)


class UpdateStack(BaseModel):
    options: dict = Field(default_factory=dict)
    ports: dict[str, int] = Field(default_factory=dict)


class ActionBody(BaseModel):
    values: dict = Field(default_factory=dict)


def register(app: FastAPI, *, db: Database, current_session: Callable, manager: StackManager) -> None:

    def call(fn, *args):
        try:
            return fn(*args)
        except StackError as exc:
            raise HTTPException(400, str(exc)) from None

    def stack_app(app_id: str) -> dict:
        target = db.get_app(app_id)
        if target is None or target["mode"] != "stack":
            raise HTTPException(404, f"unknown service {app_id}")
        return target

    @app.get("/api/stacks/templates")
    def templates(_=Depends(current_session)) -> list[dict]:
        return [t.public() for t in TEMPLATES.values()]

    @app.post("/api/stacks")
    async def create(body: CreateStack, s=Depends(current_session)) -> dict:
        return await asyncio.to_thread(call, manager.create, body.template, body.id.strip(), body.options,
                                       body.ports, body.domain, s.username)

    @app.get("/api/apps/{app_id}/stack")
    async def info(app_id: str, _=Depends(current_session)) -> dict:
        stack_app(app_id)
        return await asyncio.to_thread(call, manager.info, app_id)

    @app.put("/api/apps/{app_id}/stack")
    async def update(app_id: str, body: UpdateStack, s=Depends(current_session)) -> dict:
        stack_app(app_id)
        return {"release_id": await asyncio.to_thread(call, manager.update, app_id, body.options, body.ports,
                                                      s.username)}

    @app.get("/api/apps/{app_id}/stack/logs")
    async def logs(app_id: str, _=Depends(current_session)) -> dict:
        stack_app(app_id)
        return {"text": await asyncio.to_thread(call, manager.logs, app_id)}

    @app.post("/api/apps/{app_id}/stack/actions/{action}")
    async def action(app_id: str, action: str, body: ActionBody, s=Depends(current_session)) -> dict:
        stack_app(app_id)
        try:
            output = await asyncio.to_thread(call, manager.action, app_id, action, body.values)
        except HTTPException as exc:
            db.audit(s.username, f"stack-{action}", False, service_id=app_id, detail=str(exc.detail)[:300])
            raise
        db.audit(s.username, f"stack-{action}", True, service_id=app_id)
        return {"output": output}

    @app.post("/api/apps/{app_id}/stack/{op}")
    async def control(app_id: str, op: str, s=Depends(current_session)) -> dict:
        stack_app(app_id)
        output = await asyncio.to_thread(call, manager.control, app_id, op)
        db.audit(s.username, f"stack-{op}", True, service_id=app_id)
        return {"ok": True, "output": output}
