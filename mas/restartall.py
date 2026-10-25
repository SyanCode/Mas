"""Restart every running service of the server at once, exactly as it was.

Nothing is rebuilt, re-rendered or recreated: a container is restarted through the Docker API (same
container, same image, same volumes, ports and variables — ``docker compose up`` is never called), a
systemd unit with ``systemctl restart`` (its unit file is not rewritten), a pm2 app with ``pm2 restart``.
What was stopped stays stopped. Before and after each restart Mas reads the configuration of the
service and compares the two: the report says "identical" only when nothing changed.

Order: containers first (databases, Supabase, mail, services of the catalog), one project after the
other and, inside a project, the containers others depend on first; then the services of the server;
then the apps of Mas, one instance at a time with a health check; nginx is reloaded last (after
``nginx -t``).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from mas.actions import ActionError, UNIT_RE, nginx_reload, run_action
from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer, ReleaseLog, app_instances
from mas.host import DOCKER_SOCKET, Host
from mas.models import Manager, Status

# never restarted from here: Mas itself, and what everything else runs on
SYSTEM_UNITS = re.compile(r"^(mas|nginx|docker|containerd|ssh|sshd|systemd-.*|dbus|cron|networking)\.service$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
WAIT_UNIT = 60      # seconds for a unit to be active again
WAIT_CONTAINER = 180  # seconds for a container to run again (and pass its health check, if it has one)
STOP_GRACE = 20     # seconds a container gets to stop cleanly


class RestartError(Exception):
    pass


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def container_fingerprint(info: dict) -> dict:
    """What makes a container what it is: compared before and after the restart."""
    config, host_config = info.get("Config") or {}, info.get("HostConfig") or {}
    return {
        "id": info.get("Id"),
        "image": info.get("Image"),
        "env": _digest(sorted(config.get("Env") or [])),
        "command": _digest([config.get("Entrypoint"), config.get("Cmd")]),
        "volumes": sorted(f"{m.get('Source') or m.get('Name')}:{m.get('Destination')}:{'rw' if m.get('RW') else 'ro'}"
                          for m in info.get("Mounts") or []),
        "ports": _digest(host_config.get("PortBindings") or {}),
        "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name"),
        "networks": sorted(((info.get("NetworkSettings") or {}).get("Networks") or {}).keys()),
    }


def _depends(labels: dict) -> int:
    value = labels.get("com.docker.compose.depends_on") or ""
    return len([x for x in value.split(",") if x])


class RestartAll:
    def __init__(self, host: Host, config: Config, db: Database, deployer: Deployer, scanner) -> None:
        self.host, self.config, self.db, self.deployer, self.scanner = host, config, db, deployer, scanner
        self.sleep = time.sleep
        self._lock = threading.Lock()
        self.state: dict = {"running": False, "started_at": None, "finished_at": None, "by": None, "items": []}

    def _log(self) -> ReleaseLog:
        return ReleaseLog(Path(self.db.path).parent / "logs" / "restart-all.log")

    # -- what would be restarted ---------------------------------------------------------

    def plan(self) -> list[dict]:
        items: list[dict] = []
        items += self._containers()
        apps = self.db.list_apps()
        app_units = {u for a in apps if a["mode"] == "managed" for u, _ in app_instances(a)}
        for svc in self.scanner.result().services:
            if svc.status != Status.RUNNING:
                continue
            if svc.manager in (Manager.SYSTEMD, Manager.PHP_FPM) and svc.unit and UNIT_RE.fullmatch(svc.unit):
                if svc.unit in app_units or svc.unit.startswith("mas-app-") or SYSTEM_UNITS.match(svc.unit):
                    continue
                if any(i["key"] == f"unit:{svc.unit}" for i in items):
                    continue
                items.append({"key": f"unit:{svc.unit}", "kind": "unit", "name": svc.display_name or svc.name,
                              "target": svc.unit, "service_id": svc.id})
            elif svc.manager == Manager.PM2 and svc.unit:
                items.append({"key": f"pm2:{svc.user}:{svc.unit}", "kind": "pm2", "name": svc.display_name or svc.name,
                              "target": svc.unit, "service_id": svc.id})
        for app in apps:
            if app["mode"] != "managed" or app.get("replica_of"):
                continue
            for unit, port in app_instances(app):
                if self._active(unit):
                    items.append({"key": f"app:{unit}", "kind": "app", "name": app["name"], "target": unit,
                                  "app_id": app["id"], "port": port})
        if self._active("nginx.service"):
            items.append({"key": "nginx", "kind": "nginx", "name": "nginx", "target": "nginx.service"})
        return [{**i, "status": "pending", "detail": ""} for i in items]

    def _containers(self) -> list[dict]:
        if not self.host.exists(DOCKER_SOCKET):
            return []
        try:
            running = self.host.docker_get("/containers/json")
        except (OSError, ValueError):
            return []
        out = []
        for c in running:
            labels = c.get("Labels") or {}
            name = ((c.get("Names") or ["/" + c["Id"][:12]])[0]).lstrip("/")
            project = labels.get("com.docker.compose.project") or ""
            out.append({"key": f"docker:{c['Id']}", "kind": "container", "name": name, "target": c["Id"],
                        "project": project or None, "_order": (project == "", project, _depends(labels), name)})
        out.sort(key=lambda i: i.pop("_order"))
        return out

    # -- run --------------------------------------------------------------------------------

    def start(self, actor: str, skip: set[str] = frozenset()) -> dict:
        with self._lock:
            if self.state["running"]:
                raise RestartError("a restart of every service is already running")
            items = [i for i in self.plan() if i["key"] not in skip]
            self.state = {"running": True, "started_at": time.time(), "finished_at": None, "by": actor, "items": items}
        threading.Thread(target=self.run, daemon=True, name="mas-restart-all").start()
        return self.state

    def run(self) -> None:
        rlog = self._log()
        rlog.step(f"Restart of every service ({len(self.state['items'])}) by {self.state['by']}")
        try:
            for item in self.state["items"]:
                item["status"] = "running"
                try:
                    if item["kind"] == "app":
                        with self.deployer._lock:  # not in the middle of a deploy or a scaling
                            status, detail = self._restart(item)
                    else:
                        status, detail = self._restart(item)
                except Exception as exc:  # one service must not stop the others
                    status, detail = "failed", str(exc)[:500]
                item["status"], item["detail"] = status, detail
                rlog.line(f"{item['name']} ({item['kind']}): {status}{' — ' + detail if detail else ''}")
        finally:
            items = self.state["items"]
            failed = [i["name"] for i in items if i["status"] in ("failed", "changed")]
            self.db.audit(self.state["by"] or "mas", "restart-all", not failed,
                          detail=f"{len(items) - len(failed)}/{len(items)} identical"
                                 + (f"; check: {', '.join(failed)}" if failed else ""))
            self.state["running"] = False
            self.state["finished_at"] = time.time()
            rlog.close()
            try:
                self.scanner.refresh()
            except Exception:
                pass

    def _restart(self, item: dict) -> tuple[str, str]:
        kind = item["kind"]
        if kind == "container":
            return self._restart_container(item["target"])
        if kind in ("unit", "app"):
            status, detail = self._restart_unit(item["target"])
            if status == "identical" and kind == "app":
                ok, why = self.deployer.health_check(f"http://127.0.0.1:{item['port']}/", None,
                                                     self.config.healthcheck_timeout)
                if not ok:
                    return "failed", f"restarted but does not answer on port {item['port']} ({why})"
            return status, detail
        if kind == "pm2":
            svc = self.scanner.get(item["service_id"])
            if svc is None:
                return "skipped", "not found anymore"
            try:
                res = run_action(self.host, svc, "restart")
            except ActionError as exc:
                return "failed", str(exc)
            return ("identical", "pm2 restart (same process definition)") if res.ok else ("failed", res.output[-300:])
        if kind == "nginx":
            res = nginx_reload(self.host)
            return ("identical", "configuration tested, then reloaded") if res.ok else ("failed", res.output[-300:])
        return "skipped", "unknown kind"

    # -- containers -----------------------------------------------------------------------

    def _inspect(self, cid: str) -> dict:
        return self.host.docker_get(f"/containers/{cid}/json")

    def _restart_container(self, cid: str) -> tuple[str, str]:
        if not CONTAINER_ID_RE.fullmatch(cid):
            return "failed", "invalid container id"
        first = self._inspect(cid)
        before = container_fingerprint(first)
        was_healthy = ((first.get("State") or {}).get("Health") or {}).get("Status") in (None, "", "healthy")
        self.host.docker_request("POST", f"/containers/{cid}/restart?t={STOP_GRACE}", timeout=STOP_GRACE + 90)
        deadline = time.monotonic() + WAIT_CONTAINER
        while True:
            info = self._inspect(cid)
            state = info.get("State") or {}
            health = (state.get("Health") or {}).get("Status")
            if state.get("Running") and (health in (None, "", "healthy") or not was_healthy):  # as it was
                break
            if not state.get("Running") and state.get("Status") in ("exited", "dead"):
                return "failed", f"stopped after the restart (exit code {state.get('ExitCode')})"
            if time.monotonic() >= deadline:
                return "failed", f"not ready after {WAIT_CONTAINER} s ({state.get('Status')}, health {health})"
            self.sleep(2)
        after = container_fingerprint(info)
        changed = [k for k in before if before[k] != after[k]]
        if changed:
            return "changed", "differs after the restart: " + ", ".join(changed)
        if health:
            return "identical", "same container, image, variables, volumes and ports; health check passed"
        return "identical", "same container, image, variables, volumes and ports"

    # -- systemd ----------------------------------------------------------------------------

    def _active(self, unit: str) -> bool:
        res = self.host.run(["systemctl", "is-active", unit])
        return res is not None and res.stdout.strip() == "active"

    def _unit_fingerprint(self, unit: str) -> str:
        """The unit files (and drop-ins) and the environment the unit starts with. Not ExecStart as
        ``systemctl show`` prints it: it carries the pid and start time of the running process."""
        res = self.host.run(["systemctl", "show", unit, "-p", "FragmentPath", "-p", "DropInPaths",
                             "-p", "Environment", "-p", "EnvironmentFiles", "-p", "WorkingDirectory", "-p", "User"])
        text = res.stdout if res is not None and res.ok else ""
        files = [line.split("=", 1)[1] for line in text.splitlines() if line.startswith(("FragmentPath=", "DropInPaths="))]
        contents = [self.host.read_text(p) or "" for f in files for p in f.split() if p]
        return _digest([sorted(text.splitlines()), contents])

    def _restart_unit(self, unit: str) -> tuple[str, str]:
        if not UNIT_RE.fullmatch(unit):
            return "failed", "invalid unit name"
        before = self._unit_fingerprint(unit)
        res = self.host.run(["systemctl", "restart", unit], timeout=120)
        if res is None or not res.ok:
            return "failed", ((res.stderr or res.stdout).strip()[-300:] if res else "systemctl not found")
        deadline = time.monotonic() + WAIT_UNIT
        while not self._active(unit):
            if time.monotonic() >= deadline:
                return "failed", f"not active {WAIT_UNIT} s after the restart"
            self.sleep(1)
        if self._unit_fingerprint(unit) != before:
            return "changed", "the unit changed during the restart"
        return "identical", "same unit file, command and environment"

    def status(self) -> dict:
        return self.state
