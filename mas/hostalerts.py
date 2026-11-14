"""Alerts about the server itself: disk nearly full, certificates close to expiry, nginx errors.

Same channels as the site notifications (the mail server set in the dashboard, plus a webhook), set once
for the whole server. A problem is announced once when it appears and once when it is gone, not at every
look: the keys of the problems currently reported are kept in the settings. The webhook address is kept
encrypted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from urllib.parse import urlsplit

from mas.config import Config
from mas.db import Database
from mas.envcrypt import EnvError
from mas.host import Host
from mas.models import Status
from mas.notify import EMAIL_RE, NotifyError, Notifier

log = logging.getLogger(__name__)

SETTING = "hostalerts"
ACTIVE = "hostalerts:active"
LAST = "hostalerts:last"
INTERVAL = 300  # seconds between two looks
RUNNING = "hostalerts:running"  # ids of the services seen running: only those can "fall"
APP_UNIT = re.compile(r"^mas-app-(.+?)(?:\.\d+)?(?:\.service)?$")
DEFAULTS = {"enabled": False, "emails": [], "disk_percent": 90, "services": True}


class HostAlerts:
    def __init__(self, db: Database, host: Host, config: Config, notifier: Notifier, scanner=None) -> None:
        self.db, self.host, self.config, self.notifier = db, host, config, notifier
        self.scanner = scanner
        self.disk_usage = shutil.disk_usage  # replaced in tests
        self.checks = None  # replaced in tests: () -> list of health Check

    # -- settings -------------------------------------------------------------------------------------

    def settings(self, reveal: bool = False) -> dict:
        raw = json.loads(self.db.get_setting(SETTING) or "null") or {}
        out = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
        webhook = self.notifier._open(raw.get("webhook_sealed", "")) if raw.get("webhook_sealed") else ""
        out["has_webhook"] = bool(webhook)
        out["webhook"] = webhook if reveal else ""
        return out

    def configure(self, body: dict) -> dict:
        emails = body.get("emails", [])
        if not isinstance(emails, list) or len(emails) > 10 or any(
                not isinstance(e, str) or not EMAIL_RE.fullmatch(e.strip()) for e in emails):
            raise NotifyError("emails: up to 10 valid addresses")
        percent = body.get("disk_percent", 90)
        if not isinstance(percent, int) or isinstance(percent, bool) or not 50 <= percent <= 99:
            raise NotifyError("disk_percent: 50 to 99")
        raw = json.loads(self.db.get_setting(SETTING) or "null") or {}
        sealed = raw.get("webhook_sealed", "")
        webhook = str(body.get("webhook", "")).strip()
        if webhook:
            parts = urlsplit(webhook)
            if parts.scheme != "https" or not parts.hostname:
                raise NotifyError("webhook: an https:// address")
            sealed = self.notifier._seal(webhook)
        elif body.get("clear_webhook"):
            sealed = ""
        self.db.set_setting(SETTING, json.dumps({
            "enabled": bool(body.get("enabled")), "emails": [e.strip() for e in emails],
            "disk_percent": percent, "services": bool(body.get("services", True)), "webhook_sealed": sealed}))
        return self.settings()

    # -- what is wrong right now ------------------------------------------------------------------------

    def problems(self) -> dict[str, tuple[str, str]]:
        """Key -> (title, detail) of what needs attention."""
        cfg = self.settings()
        found: dict[str, tuple[str, str]] = {}
        seen: set[int] = set()
        for path in ("/", self.config.apps_dir, os.path.dirname(self.config.db_path)):
            try:
                st = os.stat(path)
                if st.st_dev in seen:
                    continue
                seen.add(st.st_dev)
                usage = self.disk_usage(path)
            except OSError:
                continue
            percent = round(usage.used * 100 / usage.total) if usage.total else 0
            if percent >= cfg["disk_percent"]:
                free = usage.free / 1024 ** 3
                found[f"disk:{path}"] = (f"Disk {percent}% full ({path})", f"Only {free:.1f} GB left on the disk holding {path}.")
        if cfg["services"]:
            found.update(self.down_services())
        try:
            if self.checks is not None:
                checks = self.checks()
            else:
                from mas.health import run_checks
                checks = run_checks(self.host, self.config, dns=False)
        except Exception:  # noqa: BLE001 - a failing check must not stop the disk alerts
            log.exception("host checks failed")
            checks = []
        for c in checks:
            if c.level == "error":
                found[f"check:{c.category}:{c.title}"] = (c.title, c.detail)
        return found

    def down_services(self) -> dict[str, tuple[str, str]]:
        """Services that were running and are not anymore (a stopped service nobody ever saw running, an app
        asleep on purpose or a service that disappeared from the server raise nothing). Errored ones always do."""
        if self.scanner is None:
            return {}
        services = self.scanner.result().services
        tracked = set(json.loads(self.db.get_setting(RUNNING) or "[]"))
        tracked |= {s.id for s in services if s.status == Status.RUNNING}
        found: dict[str, tuple[str, str]] = {}
        for svc in services:
            if svc.status not in (Status.STOPPED, Status.ERRORED):
                continue
            m = APP_UNIT.match(svc.unit or "")
            if m and self.db.get_setting(f"asleep:{m.group(1)}") == "1":
                continue
            if svc.status == Status.ERRORED or svc.id in tracked:
                where = ", ".join(svc.domains[:2]) or svc.manager.value
                found[f"service:{svc.id}"] = (f"{svc.display_name or svc.name} is down", f"{svc.status.value} ({where}).")
        self.db.set_setting(RUNNING, json.dumps(sorted(tracked & {s.id for s in services})))
        return found

    # -- the background loop ------------------------------------------------------------------------------

    def tick(self, now: float | None = None, force: bool = False) -> list[str]:
        """Announce new and resolved problems; returns the keys announced."""
        cfg = self.settings(reveal=True)
        if not cfg["enabled"] or not (cfg["emails"] or cfg["webhook"]):
            return []
        now = now or time.time()
        if not force and now - float(self.db.get_setting(LAST) or 0) < INTERVAL:
            return []
        self.db.set_setting(LAST, str(now))
        current = self.problems()
        before = set(json.loads(self.db.get_setting(ACTIVE) or "[]"))
        new = [k for k in current if k not in before]
        gone = sorted(before - set(current))
        if not new and not gone:
            return []
        lines = [f"- {current[k][0]}: {current[k][1]}" for k in new]
        title = f"{len(new)} problem{'s' if len(new) > 1 else ''} on the server" if new else "Server back to normal"
        text = "\n".join(lines)
        if gone:
            text += ("\n\n" if lines else "") + "Resolved: " + ", ".join(gone)
        if self._send(cfg, title, text):
            self.db.set_setting(ACTIVE, json.dumps(sorted(current)))  # retried at the next look when nobody was reached
        self.db.audit("hostalerts", "host-alert", True, detail=f"{len(new)} new, {len(gone)} resolved")
        return new + gone

    def _send(self, cfg: dict, title: str, text: str) -> bool:
        reached = False
        if cfg["webhook"]:
            try:
                self.notifier.send_webhook(cfg["webhook"], f"Mas: {title}", text, "host", "host")
                reached = True
            except (NotifyError, EnvError) as exc:
                log.warning("host alert webhook failed: %s", exc)
        if cfg["emails"]:
            try:
                self.notifier.send_email(cfg["emails"], f"[Mas] {title}", text)
                reached = True
            except (NotifyError, EnvError) as exc:
                log.warning("host alert e-mail failed: %s", exc)
        return reached

    def test(self) -> bool:
        cfg = self.settings(reveal=True)
        return self._send(cfg, "test", "Mas can reach you about this server.")
