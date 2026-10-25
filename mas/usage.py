"""What uses the memory and the CPU of the server, everything at once (the Resources page).

Read from the cgroups of systemd (``/sys/fs/cgroup/system.slice``): every service and every Docker
container (``docker-<id>.scope``) has its own, with ``memory.current`` and ``cpu.stat``. Nothing is
sampled per process, so the whole server reads in a few milliseconds. The CPU is the use since the
previous reading (the page refreshes every few seconds); the first time, two readings half a second
apart. Rows are grouped the way the dashboard shows things: one per Mas app (all its instances), one
per compose project (a service of the catalog, Supabase…), one per other service.
"""

from __future__ import annotations

import os
import shutil
import time

from mas.config import Config
from mas.db import Database
from mas.deploy import app_instances, app_resources
from mas.host import DOCKER_SOCKET, Host
from mas.resources import Resources

SLICE = "/sys/fs/cgroup/system.slice"
USER_SLICE = "/sys/fs/cgroup/user.slice"
FIRST_SAMPLE = 0.5


def _meminfo(host: Host) -> dict[str, int]:
    out = {}
    for line in (host.read_text("/proc/meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        if rest.split() and rest.split()[0].isdigit():
            out[key] = int(rest.split()[0]) // 1024
    return out


def _cpu_times(host: Host) -> tuple[int, int] | None:
    """(busy, total) jiffies of the whole machine, from /proc/stat."""
    line = (host.read_text("/proc/stat") or "").partition("\n")[0].split()
    if not line or line[0] != "cpu":
        return None
    values = [int(v) for v in line[1:9]]
    idle = values[3] + values[4]
    return sum(values) - idle, sum(values)


class Usage:
    def __init__(self, host: Host, config: Config, db: Database, scanner, resources: Resources) -> None:
        self.host, self.config, self.db, self.scanner, self.resources = host, config, db, scanner, resources
        self._last: dict[str, tuple[float, int]] = {}  # cgroup → (time, usage_usec) at the previous reading
        self._last_machine: tuple[float, int, int] | None = None

    # -- cgroups ----------------------------------------------------------------------------

    def _number(self, path: str) -> int | None:
        text = (self.host.read_text(path) or "").strip()
        return int(text) if text.isdigit() else None

    def _usage_usec(self, group: str) -> int | None:
        for line in (self.host.read_text(f"{group}/cpu.stat") or "").splitlines():
            if line.startswith("usage_usec "):
                return int(line.split()[1])
        return None

    def _groups(self) -> dict[str, str]:
        """cgroup name → path, for every service and container of system.slice."""
        out = {}
        for name in self.host.listdir(SLICE):
            if name.endswith((".service", ".scope")) and self.host.is_dir(f"{SLICE}/{name}"):
                out[name] = f"{SLICE}/{name}"
            elif name.endswith(".slice"):  # instances of templates: system-postgresql.slice/postgresql@15-main.service
                for inner in self.host.listdir(f"{SLICE}/{name}"):
                    if inner.endswith(".service") and self.host.is_dir(f"{SLICE}/{name}/{inner}"):
                        out[inner] = f"{SLICE}/{name}/{inner}"
        if self.host.is_dir(USER_SLICE):
            out["user.slice"] = USER_SLICE
        return out

    def _read(self, groups: dict[str, str]) -> dict[str, dict]:
        now = time.monotonic()
        usage = {name: self._usage_usec(path) for name, path in groups.items()}
        if any(name not in self._last for name in groups) and FIRST_SAMPLE:  # no previous reading: take one now
            time.sleep(FIRST_SAMPLE)
            before, since = usage, now
            now = time.monotonic()
            usage = {name: self._usage_usec(path) for name, path in groups.items()}
            for name, used in before.items():
                if used is not None and name not in self._last:
                    self._last[name] = (since, used)
        out = {}
        for name, path in groups.items():
            used, previous = usage[name], self._last.get(name)
            cpu = None
            if used is not None and previous and now > previous[0] and used >= previous[1]:
                cpu = round((used - previous[1]) / 1e6 / (now - previous[0]) * 100, 1)
            if used is not None:
                self._last[name] = (now, used)
            memory = self._number(f"{path}/memory.current")
            if memory is not None:  # without the file cache the kernel takes back at once (like docker stats)
                for line in (self.host.read_text(f"{path}/memory.stat") or "").splitlines():
                    if line.startswith("inactive_file "):
                        memory = max(0, memory - int(line.split()[1]))
                        break
            limit = self._number(f"{path}/memory.max")  # "max" (no limit) is not a number
            out[name] = {"memory_mb": round(memory / 1048576, 1) if memory is not None else None,
                         "cpu_percent": cpu, "limit_mb": round(limit / 1048576) if limit else None}
        for gone in set(self._last) - set(groups):
            self._last.pop(gone, None)
        return out

    # -- the page -----------------------------------------------------------------------------

    def server(self) -> dict:
        mem = _meminfo(self.host)
        cpus = os.cpu_count() or 1
        times, now = _cpu_times(self.host), time.monotonic()
        cpu = None
        if times and self._last_machine and times[1] > self._last_machine[2]:
            cpu = round((times[0] - self._last_machine[1]) / (times[1] - self._last_machine[2]) * 100, 1)
        if times:
            self._last_machine = (now, *times)
        load = [float(x) for x in (self.host.read_text("/proc/loadavg") or "0 0 0").split()[:3]]
        try:
            disk = shutil.disk_usage(self.host.real("/"))
            disk_info = {"total_gb": round(disk.total / 1e9, 1), "used_gb": round((disk.total - disk.free) / 1e9, 1)}
        except OSError:
            disk_info = None
        total, available = mem.get("MemTotal"), mem.get("MemAvailable")
        return {"cpus": cpus, "cpu_percent": cpu, "load": load,
                "memory": {"total_mb": total, "used_mb": total - available if total and available is not None else None,
                           "available_mb": available},
                "swap": {"total_mb": mem.get("SwapTotal"), "used_mb": (mem.get("SwapTotal") or 0) - (mem.get("SwapFree") or 0)},
                "disk": disk_info}

    def _containers(self) -> dict[str, dict]:
        """docker-<id>.scope → {name, project}."""
        if not self.host.exists(DOCKER_SOCKET):
            return {}
        try:
            running = self.host.docker_get("/containers/json")
        except (OSError, ValueError):
            return {}
        return {f"docker-{c['Id']}.scope": {"id": c["Id"], "name": ((c.get("Names") or ["/" + c["Id"][:12]])[0]).lstrip("/"),
                                             "project": (c.get("Labels") or {}).get("com.docker.compose.project"),
                                             "image": c.get("Image") or ""}
                for c in running}

    def _container_advice(self, info: dict) -> list[dict]:
        """A Minecraft server (itzg image) started with MEMORY only reserves all of it, even empty."""
        if not info["image"].startswith(("itzg/minecraft-server", "docker.io/itzg/minecraft-server")):
            return []
        try:
            env = dict(e.partition("=")[::2] for e in (self.host.docker_get(f"/containers/{info['id']}/json")
                                                       .get("Config") or {}).get("Env") or [])
        except (OSError, ValueError):
            return []
        if env.get("MEMORY") and not env.get("INIT_MEMORY"):
            return [{"id": "minecraft-heap", "level": "info", "params": {"name": info["name"], "memory": env["MEMORY"]},
                     "set": None}]
        return []

    def overview(self) -> dict:
        first = self._last_machine is None
        server = self.server()
        groups = self._groups()
        readings = self._read(groups)
        if first:  # the half second the cgroups were sampled over gives the CPU of the machine too
            server["cpu_percent"] = self.server()["cpu_percent"]
        containers = self._containers()
        apps = self.db.list_apps()
        services = self.scanner.result().services
        by_unit = {s.unit: s for s in services if s.unit}
        rows: dict[str, dict] = {}

        def row(key: str, **fields) -> dict:
            if key not in rows:
                rows[key] = {"key": key, "memory_mb": 0.0, "cpu_percent": None, "parts": [], **fields}
            return rows[key]

        def add(target: dict, part: str, reading: dict) -> None:
            target["parts"].append(part)
            target["memory_mb"] = round(target["memory_mb"] + (reading["memory_mb"] or 0), 1)
            if reading["cpu_percent"] is not None:
                target["cpu_percent"] = round((target["cpu_percent"] or 0) + reading["cpu_percent"], 1)

        app_of_unit = {u: a for a in apps if a["mode"] == "managed" for u, _ in app_instances(a)}
        stack_projects = {f"mas-{a['id']}": a for a in apps if a["mode"] == "stack"}
        for name, reading in readings.items():
            if name in app_of_unit:
                app = app_of_unit[name]
                add(row(f"app:{app['id']}", kind="app", name=app["name"], app_id=app["id"]), name, reading)
            elif name in containers:
                info = containers[name]
                project = info["project"]
                if project in stack_projects:
                    app = stack_projects[project]
                    target = row(f"app:{app['id']}", kind="stack", name=app["name"], app_id=app["id"])
                elif project == "mas-supabase":
                    target = row("supabase", kind="supabase", name="Supabase")
                elif project:
                    target = row(f"project:{project}", kind="containers", name=project)
                else:
                    svc = by_unit.get(info["name"])
                    target = row(f"container:{info['name']}", kind="containers", name=info["name"],
                                 service_id=svc.id if svc else None)
                add(target, info["name"], reading)
                target.setdefault("advice", []).extend(self._container_advice(info))
            elif name == "mas.service":
                add(row("mas", kind="mas", name="Mas"), name, reading)
            elif name == "user.slice":
                add(row("user", kind="user", name="user.slice"), name, reading)
            elif name.endswith(".service"):
                svc = by_unit.get(name)
                target = row(f"unit:{name}", kind="service", name=(svc.display_name or svc.name) if svc else name[:-8],
                             service_id=svc.id if svc else None, unit=name)
                if reading["limit_mb"]:
                    target["limit_mb"] = reading["limit_mb"]
                add(target, name, reading)

        for app in apps:  # what Mas knows of its apps: limits, sleep, instances, advice
            if app["mode"] != "managed" or app.get("replica_of"):
                continue
            res = app_resources(app)
            target = rows.get(f"app:{app['id']}") or row(f"app:{app['id']}", kind="app", name=app["name"], app_id=app["id"])
            target.update(limit_mb=res.get("memory_mb"), sleep_after_min=res.get("sleep_after_min"),
                          autoscale=res.get("autoscale"), asleep=self.resources.asleep(app["id"]),
                          instances=len(app_instances(app)), resources=res,
                          advice=self.resources.advice(app))

        listed = [r for r in rows.values() if r["memory_mb"] or r.get("app_id")]
        listed.sort(key=lambda r: -(r["memory_mb"] or 0))
        counted = sum(r["memory_mb"] for r in listed)
        used = server["memory"]["used_mb"]
        return {"server": server, "rows": listed, "cgroups": bool(groups),
                "kernel_and_cache_mb": round(max(0, used - counted), 1) if used is not None else None}
