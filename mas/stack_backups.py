"""Backups of the ready-made services: their data folder, every night and on demand.

A backup is ``/var/lib/mas/backups/stacks/<id>/<id>-<date>.tar.gz`` holding ``data/``. The service is
stopped during the copy (a Minecraft world or a mailbox copied while written could not be restored),
then started again; the nightly one runs between 3 and 5 a.m. (server time), at most once a day.
Restoring puts the current data aside in ``data.before-restore`` first. Backups outlive the service:
removing it keeps them.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from mas.stacks import StackError, StackManager

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}-\d{8}-\d{6}\.tar\.gz$")
NIGHT = range(3, 5)  # hours of the nightly backup
DAY = 20 * 3600  # at most one nightly backup per window


class BackupError(Exception):
    pass


class StackBackups:
    def __init__(self, stacks: StackManager, keep: int = 7) -> None:
        self.stacks, self.host, self.db = stacks, stacks.host, stacks.db
        self.keep = keep
        self.root = Path(self.db.path).parent / "backups" / "stacks"
        self._locks: dict[str, threading.Lock] = {}
        self.jobs: dict[str, dict] = {}  # app id -> running or last operation

    def dir(self, app_id: str) -> Path:
        return self.root / app_id

    def list(self, app_id: str) -> list[dict]:
        folder = self.dir(app_id)
        out = []
        for p in sorted(folder.glob(f"{app_id}-*.tar.gz"), reverse=True) if folder.is_dir() else []:
            out.append({"name": p.name, "size": p.stat().st_size,
                        "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
        return out

    def _app(self, app_id: str) -> dict:
        app = self.db.get_app(app_id)
        if app is None or app["mode"] != "stack":
            raise BackupError(f"unknown service {app_id}")
        return app

    def _running(self, app: dict) -> bool:
        res = self.host.run(self.stacks._cmd(app, ["ps", "--format", "json"]), timeout=30)
        for line in (res.stdout.splitlines() if res is not None and res.ok else []):
            try:
                items = json.loads(line)
            except ValueError:
                continue
            if any(c.get("State") == "running" for c in (items if isinstance(items, list) else [items])):
                return True
        return False

    def _tar(self, argv: list[str]) -> None:
        res = self.host.run(argv, timeout=6 * 3600)
        if res is None or not res.ok:
            raise BackupError(f"{argv[0]} failed: {(res.stderr if res else 'missing').strip()[-500:]}")

    # -- operations ----------------------------------------------------------------------

    def backup(self, app_id: str) -> str:
        """Stop, copy data/, start again. Returns the archive name."""
        app = self._app(app_id)
        folder = self.stacks.dir(app_id)
        if not self.host.is_dir(f"{folder}/data"):
            raise BackupError("this service has no data yet: deploy it first")
        with self._locks.setdefault(app_id, threading.Lock()):
            target = self.dir(app_id)
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            name = f"{app_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
            running = self._running(app)
            try:
                if running:
                    self.stacks.compose(app, ["stop"], timeout=600)
                self._tar(["tar", "czf", str(target / name), "-C", str(self.host.real(folder)), "data"])
            except (BackupError, StackError):
                (target / name).unlink(missing_ok=True)
                raise
            finally:
                if running:
                    self.stacks.compose(app, ["start"], timeout=600)
            for old in sorted(target.glob(f"{app_id}-*.tar.gz"), reverse=True)[self.keep:]:
                old.unlink(missing_ok=True)
            return name

    def restore(self, app_id: str, name: str) -> str:
        """Put the current data aside, unpack the backup, start the service."""
        app = self._app(app_id)
        archive = self.dir(app_id) / name
        if not NAME_RE.fullmatch(name) or not name.startswith(f"{app_id}-") or not archive.is_file():
            raise BackupError("unknown backup")
        folder = self.stacks.dir(app_id)
        data, aside = f"{folder}/data", f"{folder}/data.before-restore"
        with self._locks.setdefault(app_id, threading.Lock()):
            try:
                self.stacks.compose(app, ["stop"], timeout=600)
            except StackError as exc:
                log.info("stopping %s before the restore: %s", app_id, exc)
            self.host.remove(aside)  # only the last one is kept
            if self.host.exists(data):
                self.host.rename(data, aside)
            try:
                self._tar(["tar", "xzf", str(archive), "-C", str(self.host.real(folder))])
            except BackupError:
                self.host.remove(data)
                if self.host.exists(aside):
                    self.host.rename(aside, data)  # as it was
                self.stacks.compose(app, ["up", "-d"], timeout=600)
                raise
            self.host.real(data).chmod(0o755)
            self.stacks.compose(app, ["up", "-d"], timeout=600)
            return f"restored {name}; the previous data is in {aside}"

    # -- in the background (dashboard) ---------------------------------------------------

    def start(self, app_id: str, op: str, actor: str, name: str | None = None) -> dict:
        self._app(app_id)
        job = self.jobs.get(app_id)
        if job and job["running"]:
            raise BackupError(f"a {job['op']} is already running")
        if op == "restore" and (not name or not NAME_RE.fullmatch(name)):
            raise BackupError("unknown backup")
        job = {"op": op, "running": True, "started_at": time.time(), "error": None, "result": None}
        self.jobs[app_id] = job

        def run() -> None:
            try:
                job["result"] = self.backup(app_id) if op == "backup" else self.restore(app_id, name or "")
                self.db.audit(actor, f"stack-{op}", True, service_id=app_id, detail=str(job["result"])[:300])
            except (BackupError, StackError) as exc:
                job["error"] = str(exc)
                self.db.audit(actor, f"stack-{op}", False, service_id=app_id, detail=str(exc)[:300])
            except Exception as exc:  # noqa: BLE001 - shown in the dashboard
                log.exception("%s of %s failed", op, app_id)
                job["error"] = repr(exc)
            finally:
                job["running"] = False

        threading.Thread(target=run, daemon=True, name=f"mas-{op}-{app_id}").start()
        return job

    def auto_backup(self, now: datetime | None = None) -> list[str]:
        """Called periodically: one backup a night per deployed service."""
        now = now or datetime.now()
        if now.hour not in NIGHT:
            return []
        done = []
        for app in self.db.list_apps():
            if app["mode"] != "stack" or not app["current_release"]:
                continue
            key = f"stack_backup:{app['id']}"
            last = float(self.db.get_setting(key) or 0)
            if now.timestamp() - last < DAY:
                continue
            self.db.set_setting(key, str(now.timestamp()))  # once a night, even if it fails
            try:
                done.append(self.backup(app["id"]))
                self.db.audit("mas", "stack-backup", True, service_id=app["id"], detail="nightly")
            except (BackupError, StackError) as exc:
                log.warning("nightly backup of %s failed: %s", app["id"], exc)
                self.db.audit("mas", "stack-backup", False, service_id=app["id"], detail=str(exc)[:300])
        return done
