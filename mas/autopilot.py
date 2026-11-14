"""Autopilot of the advisor: the proposals are applied by the robot on their own, one after the other, and closed.

Two ways to start it: one click on *Apply everything* (every waiting proposal is queued and done in a row, then it
stops), or the standing option *apply the new proposals as they come* (off by default). Each proposal that names one
of the sites Mas deploys is handed to the robot in ``auto`` mode (it commits, deploys, checks the result; Mas still
tests nginx, health-checks the new release and rolls back by itself), then marked *done* with the robot's report, or
*failed* when the robot could not do it twice (a person then decides). Proposals about the server itself (memory,
certificates…) have no tool for the robot: they stay in the list for a person.

The limit is the AI's: when every account is at its usage limit nothing starts, and a run cut short by the limit goes
back to the queue without counting as a failure; the work resumes when the first account is back. Other brakes: one run
at a time, an optional cap per day (none by default), a failed proposal waits an hour before its second try, and three
failures in a row pause everything until it is started again.
"""

from __future__ import annotations

import json
import logging
import time

from mas.ai import AiError, AiPool
from mas.db import Database
from mas.robot import Robot

log = logging.getLogger(__name__)

SETTING = "autopilot"
FAILURES, PAUSED, BATCH = "autopilot_failures", "autopilot_paused", "autopilot_batch"
MAX_ATTEMPTS = 2  # tries per proposal
RETRY_AFTER = 3600
PAUSE_AFTER = 3  # failures in a row
SEVERITY = {"critical": 0, "warning": 1, "info": 2}
DEFAULTS = {"enabled": False, "max_per_day": 0}  # 0: no cap, the AI's own limit is the limit


class Autopilot:
    def __init__(self, db: Database, robot: Robot, pool: AiPool) -> None:
        self.db, self.robot, self.pool = db, robot, pool

    # -- settings ----------------------------------------------------------------------------------

    def settings(self) -> dict:
        raw = json.loads(self.db.get_setting(SETTING) or "null") or {}
        out = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
        now = time.time()
        rows = self.db.list_suggestions()
        out["paused"] = self.db.get_setting(PAUSED) or ""
        out["started_today"] = sum(1 for s in rows if (s.get("started_at") or 0) > now - 86400)
        out["working"] = next((s["id"] for s in rows if s["status"] == "working"), None)
        out["applied"] = sum(1 for s in rows if s["status"] == "done" and s.get("run_id"))
        out["batch"] = self.db.get_setting(BATCH) == "1"
        waiting = [s for s in rows if s["status"] == "new" and s["attempts"] < MAX_ATTEMPTS]
        out["left"] = sum(1 for s in waiting if self.app_for(s["target"]))
        out["manual"] = len(waiting) - out["left"]  # about the server itself: nothing the robot can do
        limited = [a["cooldown_until"] for a in self.db.list_ai_accounts()
                   if a["enabled"] and a["cooldown_until"] and a["cooldown_until"] > now]
        out["limited_until"] = min(limited) if limited and not self.pool.usable() else None
        return out

    def configure(self, enabled: bool | None = None, max_per_day: int | None = None) -> dict:
        raw = {k: self.settings()[k] for k in DEFAULTS}
        if max_per_day is not None:
            if isinstance(max_per_day, bool) or not isinstance(max_per_day, int) or not 0 <= max_per_day <= 50:
                raise AiError("max_per_day: 0 (no cap) to 50")
            raw["max_per_day"] = max_per_day
        if enabled is not None:
            raw["enabled"] = bool(enabled)
            if enabled:  # turning it on again lifts a pause
                self.db.set_setting(PAUSED, None)
                self.db.set_setting(FAILURES, None)
        self.db.set_setting(SETTING, json.dumps(raw))
        return self.settings()

    def run_all(self) -> dict:
        """One click: queue every waiting proposal the robot can do; they are applied in a row, then it stops."""
        self.db.set_setting(PAUSED, None)
        self.db.set_setting(FAILURES, None)
        self.db.set_setting(BATCH, "1")
        out = self.settings()
        return {"queued": out["left"], "manual": out["manual"]}

    # -- which proposal, which goal --------------------------------------------------------------------

    def app_for(self, target: str) -> dict | None:
        """The app a proposal is about (by id, name or domain), when the robot can work on it."""
        wanted = (target or "").strip().lower()
        if not wanted:
            return None
        for a in self.db.list_apps():
            names = {str(a.get(k) or "").lower() for k in ("id", "name", "domain")}
            if wanted in names and a["mode"] == "managed" and a.get("installation_id") and a.get("environment") != "preview":
                return a
        return None

    @staticmethod
    def goal_for(s: dict) -> str:
        return (f"{s['title']}. {s['detail']}\n\nThis is a proposal of the advisor. Make the change it asks for in the "
                "repository of the site, deploy it, check the result, and report briefly. If it needs a fact only a "
                "human has, leave a clear placeholder and say so.")

    # -- the background loop ---------------------------------------------------------------------------------

    def settle(self) -> list[str]:
        """Close the proposals whose run ended."""
        events = []
        for s in self.db.list_suggestions("working"):
            run = self.db.get_robot_run(s["run_id"]) if s.get("run_id") else None
            if run and run["status"] in ("running", "waiting"):
                continue
            now = time.time()
            if run and run["status"] == "done":
                self.db.update_suggestion(s["id"], status="done", result=(run.get("summary") or "")[:1500], finished_at=now)
                self.db.set_setting(FAILURES, None)
                self.db.audit("autopilot", "autopilot-done", True, service_id=s["target"] or None, detail=s["title"][:200])
                events.append(f"done:{s['id']}")
                continue
            if not self.pool.usable():  # cut short by the AI's usage limit: not a failure, back to the queue
                self.db.update_suggestion(s["id"], status="new", result="waiting for the AI limit to reset", finished_at=None)
                events.append(f"limit:{s['id']}")
                continue
            attempts = s["attempts"] + 1
            reason = (run.get("summary") if run else "") or "the run did not finish"
            self.db.update_suggestion(s["id"], status="new" if attempts < MAX_ATTEMPTS else "failed", attempts=attempts,
                                      result=reason[:1500], finished_at=now)
            self.db.audit("autopilot", "autopilot-failed", False, service_id=s["target"] or None,
                          detail=f"{s['title'][:120]}: {reason[:150]}")
            streak = int(self.db.get_setting(FAILURES) or 0) + 1
            self.db.set_setting(FAILURES, str(streak))
            if streak >= PAUSE_AFTER:
                self.db.set_setting(PAUSED, f"{streak} runs failed in a row: look at the last reports, then turn it on again")
            events.append(f"failed:{s['id']}")
        return events

    def tick(self, user_id: int | None) -> list[str]:
        events = self.settle()
        cfg = self.settings()
        if cfg["batch"] and not cfg["left"] and cfg["working"] is None:  # everything it could do is done
            self.db.set_setting(BATCH, None)
            return events
        if (not (cfg["enabled"] or cfg["batch"]) or cfg["paused"] or user_id is None or self.robot.active() is not None
                or not self.pool.configured() or not self.pool.usable()
                or (cfg["max_per_day"] and cfg["started_today"] >= cfg["max_per_day"])):
            return events
        now = time.time()
        waiting = [s for s in self.db.list_suggestions("new")
                   if s["attempts"] < MAX_ATTEMPTS and now - (s.get("finished_at") or 0) >= RETRY_AFTER * (1 if s["attempts"] else 0)
                   and self.app_for(s["target"])]
        if not waiting:
            return events
        s = min(waiting, key=lambda x: (SEVERITY.get(x["severity"], 3), x["id"]))
        app = self.app_for(s["target"])
        try:
            run_id = self.robot.start(self.goal_for(s), app["id"], "auto", user_id)
        except AiError as exc:  # busy or no account: next time
            log.info("autopilot waits: %s", exc)
            return events
        self.db.update_suggestion(s["id"], status="working", run_id=run_id, started_at=now)
        self.db.audit("autopilot", "autopilot-start", True, service_id=app["id"], detail=s["title"][:200])
        events.append(f"started:{s['id']}")
        return events
