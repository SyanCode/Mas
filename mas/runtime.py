"""Live state of a detected service: memory, CPU, uptime, restarts, listening ports and links. Read-only.

The scan says what runs; this module says how it is doing right now, from ``/proc``, ``systemctl show``
and the Docker API. It is read on demand by the service page, never during the scan.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath

from mas.collectors import ScanContext
from mas.collectors import ports as ports_collector
from mas.config import Config
from mas.db import Database
from mas.host import Host
from mas.models import Manager, Service

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
MAX_PIDS = 2000
UNSET = 2**64 - 1  # systemd prints this (or "[not set]") when a counter is off
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9*](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)
SYSTEMD_PROPS = ("Description", "UnitFileState", "NRestarts", "MemoryCurrent", "MemoryPeak", "TasksCurrent",
                 "CPUUsageNSec", "ActiveEnterTimestampMonotonic", "FragmentPath", "ActiveState")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _stat(host: Host, pid: int) -> tuple[int, int, int] | None:
    """(ppid, CPU ticks used, start time in ticks after boot) from /proc/<pid>/stat."""
    text = host.read_text(f"/proc/{pid}/stat")
    if not text or ")" not in text:
        return None
    fields = text.rsplit(")", 1)[1].split()
    try:
        return int(fields[1]), int(fields[11]) + int(fields[12]), int(fields[19])
    except (IndexError, ValueError):
        return None


def _status(host: Host, pid: int) -> dict[str, str]:
    out = {}
    for line in (host.read_text(f"/proc/{pid}/status") or "").splitlines():
        key, _, value = line.partition(":")
        out[key] = value.strip()
    return out


def process_tree(host: Host, root: int) -> list[int]:
    """The process and all its descendants (workers, child processes)."""
    children: dict[int, list[int]] = {}
    for name in host.listdir("/proc")[: MAX_PIDS * 10]:
        if name.isdigit():
            st = _stat(host, int(name))
            if st:
                children.setdefault(st[0], []).append(int(name))
    tree, todo = [], [root]
    while todo and len(tree) < MAX_PIDS:
        pid = todo.pop()
        tree.append(pid)
        todo.extend(children.get(pid, []))
    return tree


def _boot_time(host: Host) -> float | None:
    for line in (host.read_text("/proc/stat") or "").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    return None


# Previous CPU reading per process tree: the next call measures against it instead of sleeping.
_LAST_CPU: dict[int, tuple[float, int]] = {}


def process_stats(host: Host, pid: int, sample: float = 0.25) -> dict | None:
    """Memory (RSS of the whole tree), CPU since the previous call (or over ``sample`` seconds the
    first time), start time, threads."""
    root = _stat(host, pid)
    if root is None:
        return None
    tree = process_tree(host, pid)
    ticks = lambda: sum((_stat(host, p) or (0, 0, 0))[1] for p in tree)  # noqa: E731
    previous = _LAST_CPU.get(pid)
    if previous and 0 < time.monotonic() - previous[0] < 120:
        since, before = previous
    elif sample:
        since, before = time.monotonic(), ticks()
        time.sleep(sample)
    else:
        since = before = None
    after, at = ticks(), time.monotonic()
    _LAST_CPU[pid] = (at, after)
    if len(_LAST_CPU) > 500:  # processes come and go
        for old in sorted(_LAST_CPU, key=lambda k: _LAST_CPU[k][0])[:250]:
            _LAST_CPU.pop(old, None)
    rss = threads = 0
    for p in tree:
        status = _status(host, p)
        rss += int((status.get("VmRSS") or "0 kB").split()[0])
        threads += int(status.get("Threads") or 0)
    boot = _boot_time(host)
    cpu = None
    if since is not None and at > since:
        cpu = round(max(0, after - before) / CLK_TCK / (at - since) * 100, 1)
    return {"pids": tree, "processes": len(tree), "threads": threads, "memory_mb": round(rss / 1024, 1),
            "cpu_percent": cpu, "started_at": _iso(boot + root[2] / CLK_TCK) if boot else None}


def _number(value: str | None) -> int | None:
    if not value or not value.isdigit() or int(value) >= UNSET:
        return None
    return int(value)


def systemd_stats(host: Host, unit: str) -> dict | None:
    res = host.run(["systemctl", "show", unit, "--no-pager", *(f"-p{p}" for p in SYSTEMD_PROPS)])
    if res is None or not res.ok:
        return None
    props = dict(line.partition("=")[::2] for line in res.stdout.splitlines() if "=" in line)
    memory, peak = _number(props.get("MemoryCurrent")), _number(props.get("MemoryPeak"))
    cpu, since = _number(props.get("CPUUsageNSec")), _number(props.get("ActiveEnterTimestampMonotonic"))
    uptime = host.read_text("/proc/uptime")
    started = None
    if since and uptime:
        started = _iso(time.time() - float(uptime.split()[0]) + since / 1e6)
    return {"unit": unit, "description": props.get("Description") or None,
            "enabled": props.get("UnitFileState") or None, "state": props.get("ActiveState") or None,
            "restarts": _number(props.get("NRestarts")), "tasks": _number(props.get("TasksCurrent")),
            "memory_mb": round(memory / 1048576, 1) if memory is not None else None,
            "memory_peak_mb": round(peak / 1048576, 1) if peak is not None else None,
            "cpu_seconds": round(cpu / 1e9, 1) if cpu is not None else None,
            "started_at": started, "unit_file": props.get("FragmentPath") or None}


def docker_stats(host: Host, container_id: str) -> dict | None:
    try:
        info = host.docker_get(f"/containers/{container_id}/json")
    except (OSError, ValueError):
        return None
    state, config = info.get("State") or {}, info.get("Config") or {}
    out = {"image": config.get("Image"), "started_at": state.get("StartedAt") if state.get("Running") else None,
           "health": (state.get("Health") or {}).get("Status"), "restarts": info.get("RestartCount"),
           "restart_policy": ((info.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or None,
           "compose_project": (config.get("Labels") or {}).get("com.docker.compose.project"),
           "ports": [], "memory_mb": None, "memory_limit_mb": None, "cpu_percent": None}
    for private, bindings in ((info.get("NetworkSettings") or {}).get("Ports") or {}).items():
        for b in bindings or []:
            if b.get("HostPort", "").isdigit():
                out["ports"].append({"port": int(b["HostPort"]), "address": b.get("HostIp") or "0.0.0.0",
                                     "target": private})
    if state.get("Running"):
        try:
            stats = host.docker_get(f"/containers/{container_id}/stats?stream=false")
        except (OSError, ValueError):
            stats = {}
        mem = stats.get("memory_stats") or {}
        if mem.get("usage"):
            cache = (mem.get("stats") or {}).get("inactive_file", 0)
            out["memory_mb"] = round((mem["usage"] - cache) / 1048576, 1)
            total = next((int(line.split()[1]) * 1024 for line in (host.read_text("/proc/meminfo") or "").splitlines()
                          if line.startswith("MemTotal:")), None)
            if mem.get("limit") and (total is None or mem["limit"] < total):  # no limit: the RAM of the server
                out["memory_limit_mb"] = round(mem["limit"] / 1048576)
        cpu, pre = stats.get("cpu_stats") or {}, stats.get("precpu_stats") or {}
        used = (cpu.get("cpu_usage") or {}).get("total_usage", 0)
        delta = used - (pre.get("cpu_usage") or {}).get("total_usage", 0)
        system = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        if delta >= 0 and system > 0:
            out["cpu_percent"] = round(delta / system * (cpu.get("online_cpus") or 1) * 100, 1)
    return out


def _links(svc: Service, ports: list[dict], config: Config) -> list[dict]:
    https = bool(svc.ssl_certificate or svc.https_redirect)
    links, seen = [], set()
    for route in svc.routes or [f"{d}/" for d in svc.domains]:
        host_part, _, path = route.partition("/")
        if DOMAIN_RE.fullmatch(host_part) and "*" not in host_part:
            url = f"{'https' if https else 'http'}://{host_part}/{path}"
            if url not in seen:
                seen.add(url)
                links.append({"kind": "web", "url": url})
    server = config.public_ip or "<server>"
    for p in ports:
        if p["public"]:
            links.append({"kind": "port", "port": p["port"], "url": f"http://{server}:{p['port']}"})
        elif not links:  # private and no domain: reachable through an SSH tunnel
            links.append({"kind": "tunnel", "port": p["port"], "url": f"http://localhost:{p['port']}",
                          "command": f"ssh -L {p['port']}:127.0.0.1:{p['port']} <you>@{server}"})
    return links


def app_for(svc: Service, apps: list[dict], apps_dir: str) -> dict | None:
    """The Mas app behind this service: an adopted service, or the unit, port, nginx file or folder
    of an app Mas deploys (its site shows up in the scan like any other)."""
    base = apps_dir.rstrip("/")
    for app in apps:
        folder = f"{base}/{app['id']}"
        if (app.get("service_id") == svc.id or (svc.unit and svc.unit.startswith(f"mas-app-{app['id']}"))
                or (app.get("mode") == "managed" and svc.port and app.get("port") == svc.port)
                or any(PurePosixPath(f).name == f"mas-app-{app['id']}.conf" for f in svc.nginx_files)
                or (svc.path and (svc.path == folder or svc.path.startswith(folder + "/")))):
            return {"id": app["id"], "name": app["name"]}
    return None


def _app_for(svc: Service, db: Database, config: Config) -> dict | None:
    return app_for(svc, db.list_apps(), config.apps_dir)


def service_runtime(host: Host, config: Config, db: Database, svc: Service, sample: float = 0.25) -> dict:
    out: dict = {"process": None, "systemd": None, "docker": None, "ports": [], "links": [], "app": None}
    if svc.manager == Manager.DOCKER and svc.container_id:
        out["docker"] = docker_stats(host, svc.container_id)
        if out["docker"]:
            out["ports"] = [{"port": p["port"], "address": p["address"],
                             "public": not p["address"].startswith("127.")} for p in out["docker"]["ports"]]
    else:
        if svc.unit and svc.unit.endswith(".service") and svc.manager in (Manager.SYSTEMD, Manager.PHP_FPM):
            out["systemd"] = systemd_stats(host, svc.unit)
        if svc.pid:
            out["process"] = process_stats(host, svc.pid, sample)
        pids = set((out["process"] or {}).get("pids") or [])
        if pids:
            seen = set()
            for s in ports_collector.collect(ScanContext(host, config)):
                if s.port and pids & set(s.pids) and s.port not in seen:
                    seen.add(s.port)
                    out["ports"].append({"port": s.port, "address": s.address, "public": not s.is_loopback})
            out["ports"].sort(key=lambda p: p["port"])
    if out["process"]:
        out["process"].pop("pids", None)
    out["links"] = _links(svc, out["ports"], config)
    out["app"] = _app_for(svc, db, config)
    return out
