"""Resources of the apps: a memory limit, sleep when nobody visits, instances that follow the traffic.

- ``memory_mb``: written in the units (MemoryHigh / MemoryMax, NODE_OPTIONS for Node): an app cannot
  take the memory of the others. Applied at the next deploy.
- ``sleep_after_min``: after that many minutes without a request, the app is stopped and uses nothing;
  nginx sends the next request to Mas, which starts it and answers a "starting" page meanwhile.
- ``autoscale`` {min, max}: every minute, the instances follow the requests (TARGET_RPM per instance)
  and the CPU: one more as soon as it is needed, one less after QUIET_BEFORE_DOWN quiet seconds.
  Instances are started and stopped without a rebuild (Deployer.scale).

The requests come from the upstream log of the app (mas_upstream format, see mas.cluster).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from mas.cluster import Cluster
from mas.config import Config
from mas.db import Database
from mas.deploy import (
    MAX_INSTANCES, PROXY_TYPES, DeployError, Deployer, ReleaseLog, app_instances, app_resources, unit_name,
)
from mas.host import Host
from mas.models import ServiceType

TARGET_RPM = 600            # requests per minute one instance takes before another one starts
CPU_UP = 70.0               # average CPU % of the instances above which one more starts
CPU_DOWN = 25.0
QUIET_BEFORE_DOWN = 600     # seconds of little traffic before an instance stops
COOLDOWN = 120              # seconds between two changes of the same app
WAKE_EVERY = 5              # seconds: a sleeping app is started at most that often
MIN_MEMORY_MB, MAX_MEMORY_MB = 64, 65536
MIN_SLEEP_MIN, MAX_SLEEP_MIN = 5, 7 * 24 * 60
ASLEEP = "asleep:"          # setting asleep:<app id> = "1" while the app sleeps


class ResourceError(Exception):
    pass


def validate_resources(memory_mb: int | None, sleep_after_min: int | None, autoscale: dict | None) -> dict:
    out: dict = {}
    if memory_mb:
        if not MIN_MEMORY_MB <= memory_mb <= MAX_MEMORY_MB:
            raise ResourceError(f"memory: between {MIN_MEMORY_MB} and {MAX_MEMORY_MB} MB")
        out["memory_mb"] = int(memory_mb)
    if sleep_after_min:
        if not MIN_SLEEP_MIN <= sleep_after_min <= MAX_SLEEP_MIN:
            raise ResourceError(f"sleep: after {MIN_SLEEP_MIN} minutes at least, a week at most")
        out["sleep_after_min"] = int(sleep_after_min)
    if autoscale:
        low, high = int(autoscale.get("min") or 1), int(autoscale.get("max") or 1)
        if not 1 <= low <= high <= MAX_INSTANCES:
            raise ResourceError(f"instances: 1 ≤ min ≤ max ≤ {MAX_INSTANCES}")
        if high > low:
            out["autoscale"] = {"min": low, "max": high}
    if out.get("sleep_after_min") and out.get("autoscale"):
        out["autoscale"]["min"] = 1  # a sleeping app wakes up with one instance
    return out


class Resources:
    def __init__(self, host: Host, config: Config, db: Database, deployer: Deployer, cluster: Cluster) -> None:
        self.host, self.config, self.db, self.deployer, self.cluster = host, config, db, deployer, cluster
        self.started = time.time()
        self.last_seen: dict[str, float] = {}      # app id → time of its last request
        self.last_change: dict[str, float] = {}    # app id → time of the last scale / sleep / wake
        self.quiet_since: dict[str, float] = {}    # app id → since when fewer instances would do
        self.cpu: dict[str, tuple[float, int]] = {}  # unit → (time, CPUUsageNSec) at the previous tick

    def _log(self, app_id: str) -> ReleaseLog:
        return ReleaseLog(Path(self.db.path).parent / "logs" / "resources" / f"{app_id}.log")

    def asleep(self, app_id: str) -> bool:
        return self.db.get_setting(ASLEEP + app_id) == "1"

    # -- every minute --------------------------------------------------------------

    def tick(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        notes = []
        for app in self.db.list_apps():
            res = app_resources(app)
            if app["mode"] != "managed" or ServiceType(app["type"]) not in PROXY_TYPES or app.get("replica_of"):
                continue
            if not (res.get("sleep_after_min") or res.get("autoscale")):
                continue
            entries = [e for e in self.cluster._follow(app["id"], now) if e[1].startswith("127.0.0.1:")]
            if entries:
                self.last_seen[app["id"]] = max(self.last_seen.get(app["id"], 0), max(e[0] for e in entries))
            if self.asleep(app["id"]):
                continue
            try:
                note = self._sleep(app, res, now) or self._autoscale(app, res, entries, now)
            except DeployError as exc:
                note = f"{app['id']}: {exc}"
            if note:
                notes.append(note)
                self.db.audit("resources", "autoscale", True, service_id=app["id"], detail=note)
        return notes

    def _sleep(self, app: dict, res: dict, now: float) -> str | None:
        minutes = res.get("sleep_after_min")
        if not minutes or not app.get("domain"):
            return None
        seen = self.last_seen.get(app["id"], self.started)  # after a restart of Mas: counted from then
        if now - seen < minutes * 60 or now - self.last_change.get(app["id"], 0) < COOLDOWN:
            return None
        units = [u for u, _ in app_instances(app)]
        self.db.set_setting(ASLEEP + app["id"], "1")
        self.host.run(["systemctl", "stop", *units], timeout=120)
        self.last_change[app["id"]] = now
        return f"{app['id']}: asleep after {minutes} min without a visit ({len(units)} instance(s) stopped)"

    def _autoscale(self, app: dict, res: dict, entries: list, now: float) -> str | None:
        auto = res.get("autoscale")
        if not auto:
            return None
        current = len(app_instances(app))
        rpm = sum(1 for e in entries if now - e[0] <= 60)
        cpu = self._cpu(app, now)
        wanted = max(auto["min"], min(auto["max"], math.ceil(rpm / TARGET_RPM) or 1))
        if cpu is not None and cpu > CPU_UP:
            wanted = max(wanted, min(auto["max"], current + 1))
        elif cpu is not None and cpu > CPU_DOWN:
            wanted = max(wanted, current)  # still busy: keep what is running
        wanted = max(auto["min"], min(auto["max"], wanted))
        if now - self.last_change.get(app["id"], 0) < COOLDOWN:
            return None
        if wanted > current:
            self.quiet_since.pop(app["id"], None)
            target = current + 1  # one at a time
        elif wanted < current:
            since = self.quiet_since.setdefault(app["id"], now)
            if now - since < QUIET_BEFORE_DOWN:
                return None
            target = current - 1
        else:
            self.quiet_since.pop(app["id"], None)
            return None
        self.deployer.scale(app["id"], target, self._log(app["id"]))
        self.last_change[app["id"]] = now
        self.quiet_since.pop(app["id"], None)
        why = f"{rpm} req/min" + (f", CPU {cpu:.0f} %" if cpu is not None else "")
        return f"{app['id']}: {current} → {target} instance(s) ({why})"

    def _cpu(self, app: dict, now: float) -> float | None:
        """Average CPU % of the instances since the previous tick (systemd accounting)."""
        values = []
        for unit, _ in app_instances(app):
            res = self.host.run(["systemctl", "show", unit, "-pCPUUsageNSec", "--value"])
            text = (res.stdout.strip() if res is not None and res.ok else "")
            if not text.isdigit():
                continue
            used = int(text)
            before = self.cpu.get(unit)
            self.cpu[unit] = (now, used)
            if before and now > before[0] and used >= before[1]:
                values.append((used - before[1]) / 1e9 / (now - before[0]) * 100)
        return sum(values) / len(values) if values else None

    # -- a visitor comes back ---------------------------------------------------------

    def wake(self, app_id: str) -> bool:
        """Start a sleeping app (called by nginx through /api/wake when it finds no backend)."""
        app = self.db.get_app(app_id)
        if app is None or not self.asleep(app_id):
            return False
        now = time.time()
        if now - self.last_change.get(app_id, 0) < WAKE_EVERY:
            return True  # starting already
        self.last_change[app_id] = now
        self.last_seen[app_id] = now
        units = [u for u, _ in app_instances(app)]  # all of them: nginx sends requests to every instance
        self.host.run(["systemctl", "start", *units], timeout=120)
        self.db.set_setting(ASLEEP + app_id, None)
        self.db.audit("resources", "wake", True, service_id=app_id, detail="a visitor came back")
        return True

    def status(self, app: dict) -> dict:
        res = app_resources(app)
        seen = self.last_seen.get(app["id"])
        return {"resources": res, "asleep": self.asleep(app["id"]), "instances": len(app_instances(app)),
                "last_request_at": seen, "target_rpm_per_instance": TARGET_RPM,
                "unit": unit_name(app["id"])}


    # -- advice ---------------------------------------------------------------------

    def memory_mb(self, app: dict) -> float | None:
        """Memory of all the instances (systemd accounting), MB."""
        total, found = 0, False
        for unit, _ in app_instances(app):
            res = self.host.run(["systemctl", "show", unit, "-pMemoryCurrent", "--value"])
            text = res.stdout.strip() if res is not None and res.ok else ""
            if text.isdigit() and int(text) < 2**63:
                total += int(text)
                found = True
        return round(total / 1048576, 1) if found else None

    def advice(self, app: dict, now: float | None = None) -> list[dict]:
        """What would make the app lighter or steadier, from what it uses now: {id, level, params, set}
        (``set``: the resources the dashboard applies in one click; the texts are in the dashboard)."""
        now = now or time.time()
        if app["mode"] != "managed" or ServiceType(app["type"]) not in PROXY_TYPES:
            return []
        res = app_resources(app)
        out: list[dict] = []
        instances = len(app_instances(app))
        memory = self.memory_mb(app)
        entries = [e for e in self.cluster._follow(app["id"], now) if e[1].startswith("127.0.0.1:")]
        rpm = round(len(entries) / 15, 1)
        if memory is not None:
            per = round(memory / max(1, instances))
            limit = res.get("memory_mb")
            if not limit:
                suggested = max(128, int(math.ceil(per * 1.5 / 64)) * 64)
                out.append({"id": "memory-limit", "level": "info", "params": {"used": per, "mb": suggested},
                            "set": {"memory_mb": suggested}})
            elif per > limit * 0.9:
                out.append({"id": "memory-high", "level": "warning", "params": {"used": per, "limit": limit},
                            "set": {"memory_mb": int(limit * 1.5)}})
        if not res.get("sleep_after_min") and app.get("domain") and rpm < 1 and now - self.started > 900:
            out.append({"id": "sleep", "level": "info", "params": {"rpm": rpm, "min": 30},
                        "set": {"sleep_after_min": 30}})
        if instances > 1 and not res.get("autoscale") and rpm * 1 < TARGET_RPM * (instances - 1):
            out.append({"id": "autoscale", "level": "info", "params": {"n": instances, "rpm": rpm},
                        "set": {"autoscale": {"min": 1, "max": instances}}})
        if app["type"] == "nextjs":
            current = f"{self.config.apps_dir.rstrip('/')}/{app['id']}/current"
            if self.host.exists(f"{current}/.next") and not self.host.exists(f"{current}/.next/standalone"):
                out.append({"id": "next-standalone", "level": "info", "params": {}, "set": None})
        return out


def dumps(value: dict) -> str | None:
    return json.dumps(value) if value else None
