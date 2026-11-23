"""API routes of the AI side: accounts (Claude, Codex…), the advisor's proposals and the robot."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from mas.advisor import STATUSES, Advisor, AdvisorError
from mas.ai import PROVIDERS, AiError, AiPool
from mas.claudelogin import ClaudeLogin, LoginError
from mas.db import Database
from mas import skills as skills_mod
from mas.robot import PRESETS, Robot


class AccountBody(BaseModel):
    name: str = Field(default="", max_length=60)
    provider: str = Field(default="anthropic", max_length=20)
    secret: str = Field(default="", max_length=700)
    model: str = Field(default="", max_length=100)
    base_url: str = Field(default="", max_length=300)
    password: str = Field(default="", max_length=200)


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    secret: str | None = Field(default=None, max_length=700)
    password: str | None = Field(default=None, max_length=200)


class LoginStart(BaseModel):
    password: str = Field(default="", max_length=200)


class LoginCode(BaseModel):
    code: str = Field(max_length=400)
    name: str = Field(default="", max_length=60)


class MoveBody(BaseModel):
    direction: int = Field(ge=-1, le=1)


class AdvisorBody(BaseModel):
    interval: str | None = Field(default=None, max_length=10)


class AutopilotBody(BaseModel):
    enabled: bool | None = None
    max_per_day: int | None = Field(default=None, ge=0, le=50)


class StatusBody(BaseModel):
    status: str = Field(max_length=10)


class RunBody(BaseModel):
    goal: str = Field(max_length=4000)
    app_id: str = Field(default="", max_length=100)
    autonomy: str | None = Field(default=None, max_length=5)


class DecisionBody(BaseModel):
    approve: bool


class RobotSettings(BaseModel):
    schedule: str | None = Field(default=None, max_length=10)
    autonomy: str | None = Field(default=None, max_length=5)


def register(app: FastAPI, *, db: Database, current_session: Callable, advisor: Advisor, pool: AiPool, robot: Robot,
             require_password: Callable, claude_login: ClaudeLogin, autopilot) -> None:

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    def accounts_payload() -> dict:
        return {"accounts": pool.accounts(), "providers": {k: {"label": v["label"], "model": v["model"]} for k, v in PROVIDERS.items()}}

    # -- accounts ---------------------------------------------------------------------------------

    @app.get("/api/ai/accounts")
    def accounts(_=Depends(current_session)) -> dict:
        return accounts_payload()

    @app.post("/api/ai/accounts")
    def add_account(body: AccountBody, s=Depends(current_session)) -> dict:
        require_password(s, body.password)  # a secret goes in
        try:
            pool.add(body.name, body.provider, body.secret, body.model, body.base_url)
        except AiError as exc:
            raise bad(exc) from None
        db.audit(s.username, "ai-account-add", True, detail=f"{body.provider} {body.name}"[:100])
        return accounts_payload()

    @app.put("/api/ai/accounts/{account_id}")
    def update_account(account_id: int, body: AccountPatch, s=Depends(current_session)) -> dict:
        if body.secret:
            require_password(s, body.password or "")
        try:
            pool.update(account_id, name=body.name, model=body.model, enabled=body.enabled, secret=body.secret)
        except AiError as exc:
            raise bad(exc) from None
        return accounts_payload()

    @app.post("/api/ai/accounts/{account_id}/move")
    def move_account(account_id: int, body: MoveBody, _=Depends(current_session)) -> dict:
        if not db.move_ai_account(account_id, body.direction):
            raise HTTPException(404, "unknown account")
        return accounts_payload()

    @app.delete("/api/ai/accounts/{account_id}")
    def delete_account(account_id: int, s=Depends(current_session)) -> dict:
        if not db.delete_ai_account(account_id):
            raise HTTPException(404, "unknown account")
        db.audit(s.username, "ai-account-delete", True, detail=str(account_id))
        return accounts_payload()

    # -- "Connect Claude": the official CLI login driven by Mas ------------------------------------

    @app.get("/api/ai/claude-login")
    def login_status(_=Depends(current_session)) -> dict:
        return claude_login.status()

    @app.post("/api/ai/claude-login/install")
    async def login_install(s=Depends(current_session)) -> dict:
        try:
            out = await asyncio.to_thread(claude_login.install)
        except LoginError as exc:
            raise HTTPException(409, str(exc)) from None
        db.audit(s.username, "claude-cli-install", True)
        return {**claude_login.status(), "output": out}

    @app.post("/api/ai/claude-login/start")
    async def login_start(body: LoginStart, s=Depends(current_session)) -> dict:
        require_password(s, body.password)
        try:
            return {"url": await asyncio.to_thread(claude_login.start)}
        except LoginError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/ai/claude-login/code")
    async def login_code(body: LoginCode, s=Depends(current_session)) -> dict:
        try:
            token = await asyncio.to_thread(claude_login.finish, body.code)
            pool.add(body.name or "Claude", "anthropic", token)
        except (LoginError, AiError) as exc:
            raise HTTPException(409, str(exc)) from None
        db.audit(s.username, "ai-account-add", True, detail="anthropic (Claude login)")
        return accounts_payload()

    @app.delete("/api/ai/claude-login")
    def login_cancel(_=Depends(current_session)) -> dict:
        claude_login.cancel()
        return {"ok": True}

    # -- advisor ------------------------------------------------------------------------------------

    @app.get("/api/advisor")
    def overview(_=Depends(current_session)) -> dict:
        return {**advisor.settings(), "suggestions": db.list_suggestions(), "autopilot": autopilot.settings()}

    @app.put("/api/advisor/autopilot")
    def autopilot_configure(body: AutopilotBody, s=Depends(current_session)) -> dict:
        try:
            out = autopilot.configure(body.enabled, body.max_per_day)
        except AiError as exc:
            raise bad(exc) from None
        db.audit(s.username, "autopilot-settings", True, detail=("on" if out["enabled"] else "off") + f", {out['max_per_day']}/day")
        return out

    @app.post("/api/advisor/autopilot/stop")
    async def autopilot_stop(s=Depends(current_session)) -> dict:
        stopped = autopilot.stop()
        db.audit(s.username, "autopilot-stop", True, detail="a run was stopped" if stopped else "queue cleared")
        return {"stopped": stopped, "autopilot": autopilot.settings()}

    @app.post("/api/advisor/apply-all")
    async def apply_all(s=Depends(current_session)) -> dict:
        """One click: analyse when nothing waits, then let the robot apply every waiting proposal in a row."""
        added, note = 0, ""
        if not [x for x in db.list_suggestions("new") if x["attempts"] < 2]:
            try:
                added = (await asyncio.to_thread(advisor.run))["added"]
            except AdvisorError as exc:  # too soon, or no account: the proposals that exist are still applied
                note = str(exc)
        queued = autopilot.run_all()
        db.audit(s.username, "advisor-apply-all", True, detail=f"{queued['queued']} queued, {added} new")
        return {"added": added, "note": note, **queued, **advisor.settings(), "suggestions": db.list_suggestions(),
                "autopilot": autopilot.settings()}

    @app.put("/api/advisor")
    def configure(body: AdvisorBody, _=Depends(current_session)) -> dict:
        try:
            advisor.configure(body.interval)
        except AdvisorError as exc:
            raise bad(exc) from None
        return advisor.settings()

    @app.post("/api/advisor/run")
    async def run(s=Depends(current_session)) -> dict:
        try:
            result = await asyncio.to_thread(advisor.run)
        except AdvisorError as exc:
            raise HTTPException(409, str(exc)) from None
        db.audit(s.username, "advisor-run", True, detail=f"{result['added']} new")
        return {**result, **advisor.settings(), "suggestions": db.list_suggestions(), "autopilot": autopilot.settings()}

    @app.put("/api/advisor/suggestions/{suggestion_id}")
    def set_status(suggestion_id: int, body: StatusBody, _=Depends(current_session)) -> dict:
        if body.status not in STATUSES:
            raise HTTPException(400, "status must be new, done or dismissed")
        if not db.set_suggestion_status(suggestion_id, body.status):
            raise HTTPException(404, "unknown proposal")
        if body.status == "new":  # back to review: the autopilot may try it again from scratch
            db.update_suggestion(suggestion_id, attempts=0, finished_at=None, result=None)
        return {"ok": True}

    # -- robot --------------------------------------------------------------------------------------

    @app.get("/api/robot")
    def robot_overview(_=Depends(current_session)) -> dict:
        return {**robot.settings(), "presets": list(PRESETS), "skills": skills_mod.catalog(), "active": robot.active(),
                "runs": db.list_robot_runs(15), "usage_30d": db.robot_usage(time.time() - 30 * 86400)}

    @app.get("/api/robot/active")
    def robot_active(_=Depends(current_session)) -> dict | None:
        """The run going on, if any: what the dashboard shows everywhere, and finds again after a refresh."""
        run_id = robot.active()
        run = db.get_robot_run(run_id) if run_id else None
        if not run or run["status"] not in ("running", "waiting"):
            return None
        last = next((x["text"] for x in reversed(run["steps"]) if x["kind"] != "result"), "")
        return {"id": run["id"], "goal": run["goal"][:200], "status": run["status"], "app_id": run["app_id"],
                "steps": len(run["steps"]), "last": last[:160], "started": run["created_at"]}

    @app.put("/api/robot")
    def robot_configure(body: RobotSettings, s=Depends(current_session)) -> dict:
        try:
            out = robot.configure(body.schedule, body.autonomy)
        except AiError as exc:
            raise bad(exc) from None
        db.audit(s.username, "robot-settings", True, detail=f"{out['schedule']}, {out['autonomy']}")
        return out

    @app.post("/api/robot/runs")
    async def robot_start(body: RunBody, s=Depends(current_session)) -> dict:
        try:
            run_id = robot.start(body.goal, body.app_id, body.autonomy or robot.settings()["autonomy"], s.user_id)
        except AiError as exc:
            raise HTTPException(409, str(exc)) from None
        db.audit(s.username, "robot-start", True, service_id=body.app_id or None, detail=body.goal[:200])
        return {"id": run_id}

    @app.get("/api/robot/runs/{run_id}")
    def robot_run(run_id: int, _=Depends(current_session)) -> dict:
        run = db.get_robot_run(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        return run

    @app.post("/api/robot/runs/{run_id}/decision")
    async def robot_decide(run_id: int, body: DecisionBody, s=Depends(current_session)) -> dict:
        if not robot.decide(run_id, body.approve):
            raise HTTPException(409, "nothing is waiting for an answer")
        db.audit(s.username, "robot-approve" if body.approve else "robot-refuse", True, detail=str(run_id))
        return {"ok": True}

    @app.post("/api/robot/runs/{run_id}/stop")
    async def robot_stop(run_id: int, s=Depends(current_session)) -> dict:
        if not robot.stop(run_id):
            raise HTTPException(409, "this run is not going on")
        db.audit(s.username, "robot-stop", True, detail=str(run_id))
        return {"ok": True}
