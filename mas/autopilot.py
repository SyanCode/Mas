"""Autopilot of the advisor: the proposals are applied by the robot on their own, one after the other, and closed.

Two ways to start it: one click on *Apply everything* (every waiting proposal is queued and done in a row, then it
stops), or the standing option *apply the new proposals as they come* (off by default). Each proposal is handed
to the robot in ``auto`` mode: one that names a site Mas deploys becomes a run on that site (it commits, deploys, checks
the result; Mas still tests nginx, health-checks the new release and rolls back by itself), any other one is a *server*
run with the robot's server tools (health checks and the fixes Mas offers, restarting a service). The robot ends its
report with ``RESULT: done`` or ``RESULT: needs a person``: the proposal is then closed with its report, or left to a
person with the reason (no second try, since it would say the same). A run that fails for another reason is tried once
more later.

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
from mas.robot import RUN_SECONDS, Robot

log = logging.getLogger(__name__)

SETTING = "autopilot"
FAILURES, PAUSED, BATCH = "autopilot_failures", "autopilot_paused", "autopilot_batch"
LAST_ERROR = "autopilot_error"
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
        out["left"] = len(waiting)
        retry = [s["finished_at"] + RETRY_AFTER for s in waiting if s["attempts"] and s.get("finished_at")
                 and s["finished_at"] + RETRY_AFTER > now]
        out["next_try"] = min(retry) if retry and not out["working"] else None  # a failed one waits before its second try
        out["blocked"] = self.blocked(out)
        out["error"] = self.db.get_setting(LAST_ERROR) or ""
        current = next((s for s in rows if s["status"] == "working"), None)
        run = self.db.get_robot_run(current["run_id"]) if current and current.get("run_id") else None
        steps = (run or {}).get("steps") or []
        out["current"] = {"id": current["id"], "title": current["title"], "since": current.get("started_at"),
                          "last_step": (steps[-1]["text"][:140] if steps else "")} if current else None
        limited = [a["cooldown_until"] for a in self.db.list_ai_accounts()
                   if a["enabled"] and a["cooldown_until"] and a["cooldown_until"] > now]
        out["limited_until"] = min(limited) if limited and not self.pool.usable() else None
        return out

    def blocked(self, cfg: dict) -> dict | None:
        """Why nothing starts although proposals wait: the reason and a sentence for the dashboard."""
        if not (cfg["enabled"] or cfg["batch"]) or not cfg["left"] or cfg["working"] is not None:
            return None
        if cfg["paused"]:
            return {"reason": "paused", "detail": cfg["paused"]}
        accounts = self.pool.accounts()
        if not accounts:
            return {"reason": "no_account", "detail": ""}
        if not any(a["enabled"] for a in accounts):  # every account was switched off: a refused key, usually
            return {"reason": "ai_disabled", "detail": f"{accounts[0]['name']}: {accounts[0].get('error') or 'disabled'}"}
        if not self.pool.usable():
            return None  # the usage limit: limited_until says when
        busy = self.robot.active()
        if busy is not None:
            return {"reason": "robot_busy", "detail": str(busy)}
        if cfg["max_per_day"] and cfg["started_today"] >= cfg["max_per_day"]:
            return {"reason": "cap", "detail": str(cfg["max_per_day"])}
        return None

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
        return {"queued": self.settings()["left"]}

    def stop(self) -> bool:
        """Stop what is running and leave the queue: nothing else starts until someone asks again."""
        self.db.set_setting(BATCH, None)
        cfg = self.settings()
        if cfg["enabled"]:
            self.configure(enabled=False)
        stopped = False
        runs = {s["run_id"] for s in self.db.list_suggestions("working") if s.get("run_id")}
        runs |= set(getattr(self.robot, "live", {}))  # and whatever else keeps the robot busy
        for run_id in runs:
            if hasattr(self.robot, "stop"):
                stopped = self.robot.stop(run_id) or stopped
        return stopped

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
    def goal_for(s: dict, site: bool = True) -> str:
        if site:
            return (f"{s['title']}. {s['detail']}\n\nThis is a proposal of the advisor. Make the change it asks for in the "
                    "repository of the site, deploy it, check the result, and report briefly. If it needs a fact only a "
                    "human has, leave a clear placeholder and say so.")
        about = f" (about: {s['target']})" if s["target"] else ""
        return (f"Server proposal of the advisor{about}: {s['title']}. {s['detail']}\n\nDo it with your server tools "
                "(health_checks, server_status, apply_health_fix, restart_service) when the data shows the problem is "
                "real. If it needs anything else, do not change anything and say what a person must do.")

    @staticmethod
    def verdict(summary: str) -> tuple[str | None, str]:
        """(``done`` | ``needs a person`` | None, the report without its last RESULT line)."""
        lines = (summary or "").rstrip().splitlines()
        if lines and lines[-1].strip().upper().startswith("RESULT:"):
            word = lines[-1].split(":", 1)[1].strip().lower()
            return ("needs a person" if "person" in word or "human" in word else "done"), "\n".join(lines[:-1]).strip()
        return None, (summary or "").strip()

    # -- the background loop ---------------------------------------------------------------------------------

    def stale(self, s: dict, run: dict | None) -> bool:
        """A run the database calls running but nobody is running (or one far past its time limit)."""
        if not run or run["status"] not in ("running", "waiting"):
            return False
        live = getattr(self.robot, "live", None)
        entry = live.get(s["run_id"]) if isinstance(live, dict) else None
        if live is not None and (entry is None or entry["task"].done()):
            return True
        return time.time() - (s.get("started_at") or time.time()) > RUN_SECONDS + 300

    def settle(self) -> list[str]:
        """Close the proposals whose run ended (or got stuck)."""
        events = []
        for s in self.db.list_suggestions("working"):
            run = self.db.get_robot_run(s["run_id"]) if s.get("run_id") else None
            if self.stale(s, run):
                if hasattr(self.robot, "stop"):
                    self.robot.stop(s["run_id"])
                self.db.update_robot_run(s["run_id"], status="failed", pending=None, summary="the run stopped answering",
                                         finished_at=time.time())
                run = self.db.get_robot_run(s["run_id"])
            elif run and run["status"] in ("running", "waiting"):
                continue
            now = time.time()
            if run and run["status"] == "done":
                said, report = self.verdict(run.get("summary") or "")
                if said == "needs a person":  # it looked and cannot do it: no second try, a person decides
                    self.db.update_suggestion(s["id"], status="failed", attempts=MAX_ATTEMPTS, result=report[:1500], finished_at=now)
                    self.db.audit("autopilot", "autopilot-needs-you", False, service_id=s["target"] or None, detail=s["title"][:200])
                    events.append(f"needs:{s['id']}")
                    continue
                self.db.update_suggestion(s["id"], status="done", result=report[:1500], finished_at=now)
                self.db.set_setting(FAILURES, None)
                self.db.audit("autopilot", "autopilot-done", True, service_id=s["target"] or None, detail=s["title"][:200])
                events.append(f"done:{s['id']}")
                continue
            if run and run["status"] == "stopped":  # someone pressed Stop: back to the list, not a failure
                self.db.update_suggestion(s["id"], status="new", result="stopped", finished_at=None)
                events.append(f"stopped:{s['id']}")
                continue
            if not self.pool.usable() or self.pool.over_budget():  # cut short by a usage limit: not a failure, back to the queue
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
        try:
            events = self._tick(user_id)
        except Exception as exc:  # noqa: BLE001 - shown in the dashboard instead of a silent "starting"
            log.exception("autopilot failed")
            self.db.set_setting(LAST_ERROR, f"{type(exc).__name__}: {exc}"[:300])
            return []
        self.db.set_setting(LAST_ERROR, None)
        return events

    def _tick(self, user_id: int | None) -> list[str]:
        if hasattr(self.robot, "reap"):
            self.robot.reap()  # a stuck run must not keep the robot busy for ever
        events = self.settle()
        cfg = self.settings()
        if cfg["batch"] and not cfg["left"] and cfg["working"] is None:  # everything it could do is done
            self.db.set_setting(BATCH, None)
            return events
        if (not (cfg["enabled"] or cfg["batch"]) or cfg["paused"] or user_id is None or self.robot.active() is not None
                or not self.pool.configured() or not self.pool.usable() or self.pool.over_budget()
                or (cfg["max_per_day"] and cfg["started_today"] >= cfg["max_per_day"])):
            return events
        now = time.time()
        waiting = [s for s in self.db.list_suggestions("new")
                   if s["attempts"] < MAX_ATTEMPTS and now - (s.get("finished_at") or 0) >= RETRY_AFTER * (1 if s["attempts"] else 0)]
        if not waiting:
            return events
        s = min(waiting, key=lambda x: (SEVERITY.get(x["severity"], 3), x["id"]))
        app = self.app_for(s["target"])  # a site of Mas: a run on it; anything else: a server run
        try:
            run_id = self.robot.start(self.goal_for(s, site=app is not None), app["id"] if app else "", "auto", user_id)
        except AiError as exc:  # busy or no account: next time
            log.info("autopilot waits: %s", exc)
            return events
        self.db.update_suggestion(s["id"], status="working", run_id=run_id, started_at=now)
        self.db.audit("autopilot", "autopilot-start", True, service_id=app["id"] if app else None,
                      detail=("" if app else "[server] ") + s["title"][:200])
        events.append(f"started:{s['id']}")
        return events
