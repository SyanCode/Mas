"""API routes of the Claude advisor (Settings → Claude advisor, and the proposals page)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from mas.advisor import STATUSES, Advisor, AdvisorError
from mas.db import Database


class AdvisorBody(BaseModel):
    token: str | None = Field(default=None, max_length=500)  # "" disconnects
    interval: str | None = Field(default=None, max_length=10)
    model: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)


class StatusBody(BaseModel):
    status: str = Field(max_length=10)


def register(app: FastAPI, *, db: Database, current_session: Callable, advisor: Advisor, require_password: Callable) -> None:

    @app.get("/api/advisor")
    def overview(_=Depends(current_session)) -> dict:
        return {**advisor.settings(), "suggestions": db.list_suggestions()}

    @app.put("/api/advisor")
    def configure(body: AdvisorBody, s=Depends(current_session)) -> dict:
        if body.token is not None:  # a secret goes in: it costs the password, like an API token
            require_password(s, body.password or "")
        try:
            advisor.configure(body.token, body.interval, body.model)
        except AdvisorError as exc:
            raise HTTPException(400, str(exc)) from None
        if body.token is not None:
            db.audit(s.username, "advisor-connect" if body.token else "advisor-disconnect", True)
        return advisor.settings()

    @app.post("/api/advisor/run")
    async def run(s=Depends(current_session)) -> dict:
        try:
            result = await asyncio.to_thread(advisor.run)
        except AdvisorError as exc:
            raise HTTPException(409, str(exc)) from None
        db.audit(s.username, "advisor-run", True, detail=f"{result['added']} new")
        return {**result, **advisor.settings(), "suggestions": db.list_suggestions()}

    @app.put("/api/advisor/suggestions/{suggestion_id}")
    def set_status(suggestion_id: int, body: StatusBody, _=Depends(current_session)) -> dict:
        if body.status not in STATUSES:
            raise HTTPException(400, "status must be new, done or dismissed")
        if not db.set_suggestion_status(suggestion_id, body.status):
            raise HTTPException(404, "unknown proposal")
        return {"ok": True}
