"""API routes of the resources of the apps: limits, sleep, autoscale, advice, and the wake-up of nginx."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mas import templates
from mas.db import Database
from mas.deploy import Deployer, DeployError, app_instances
from mas.resources import ResourceError, Resources, dumps, validate_resources


class AutoscaleBody(BaseModel):
    min: int = Field(ge=1, le=16)
    max: int = Field(ge=1, le=16)


class ResourcesBody(BaseModel):
    memory_mb: int | None = Field(default=None, ge=0, le=65536)
    sleep_after_min: int | None = Field(default=None, ge=0, le=10080)
    autoscale: AutoscaleBody | None = None


def register(app: FastAPI, *, db: Database, current_session: Callable, deployer: Deployer,
             resources: Resources) -> None:

    def find(app_id: str) -> dict:
        target = db.get_app(app_id)
        if target is None:
            raise HTTPException(404, "unknown app")
        return target

    @app.get("/api/apps/{app_id}/resources")
    async def get_resources(app_id: str, _=Depends(current_session)) -> dict:
        target = find(app_id)
        status = resources.status(target)
        status["memory_now_mb"] = await asyncio.to_thread(resources.memory_mb, target)
        status["advice"] = await asyncio.to_thread(resources.advice, target)
        return status

    @app.put("/api/apps/{app_id}/resources")
    def put_resources(app_id: str, body: ResourcesBody, s=Depends(current_session)) -> dict:
        target = find(app_id)
        try:
            value = validate_resources(body.memory_mb, body.sleep_after_min,
                                       body.autoscale.model_dump() if body.autoscale else None)
        except ResourceError as exc:
            raise HTTPException(400, str(exc)) from None
        db.update_app(app_id, resources=dumps(value))
        db.audit(s.username, "app-resources", True, service_id=app_id, detail=dumps(value) or "none")

        def apply() -> None:  # units and nginx site of the live release, then the instance count
            try:
                deployer.apply_resources(app_id, resources._log(app_id))
                auto = value.get("autoscale")
                current = len(app_instances(db.get_app(app_id)))
                if auto and not auto["min"] <= current <= auto["max"]:
                    deployer.scale(app_id, max(auto["min"], min(auto["max"], current)), resources._log(app_id))
            except DeployError as exc:
                db.audit("resources", "app-resources", False, service_id=app_id, detail=str(exc)[:500])

        threading.Thread(target=apply, daemon=True, name=f"mas-resources-{app_id}").start()
        return resources.status(db.get_app(app_id))

    # nginx found no backend for an app that sleeps: start it and answer a page that refreshes until it is up
    @app.api_route("/api/wake/{app_id}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   include_in_schema=False)
    async def wake(app_id: str) -> HTMLResponse:
        woke = await asyncio.to_thread(resources.wake, app_id)
        page = templates.status_page("waking" if woke else "deploying")
        return HTMLResponse(page, status_code=503, headers={"Retry-After": "2", "Cache-Control": "no-store"})
