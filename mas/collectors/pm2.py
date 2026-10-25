"""PM2 apps, for every user that has a ``~/.pm2`` directory.

``pm2 jlist`` starts a daemon when none is running, which would be a side
effect. The command is therefore only run when the user's daemon is alive,
and as that user (via ``runuser``) when Mas runs as root.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from mas.collectors import ScanContext
from mas.collectors.processes import UserEntry
from mas.host import Host

PM2_STATUS = {"online": "running", "launching": "running", "stopped": "stopped",
              "stopping": "stopped", "errored": "errored", "one-launch-status": "stopped"}


@dataclass
class Pm2App:
    name: str
    pm_id: int
    user: str
    home: str
    pid: int | None
    status: str  # running | stopped | errored | unknown
    cwd: str | None = None
    script: str | None = None
    port: int | None = None
    pm2_home: str | None = None
    log_paths: list[str] | None = None


def find_pm2(host: Host, home: str) -> str | None:
    candidates = ["/usr/local/bin/pm2", "/usr/bin/pm2"]
    candidates += host.glob(f"{home}/.nvm/versions/node/*/bin/pm2")[::-1]
    candidates += [f"{home}/.npm-global/bin/pm2", f"{home}/.local/bin/pm2"]
    return next((c for c in candidates if host.exists(c)), None)


def pm2_command(host: Host, user: UserEntry, args: list[str]) -> list[str] | None:
    """argv running ``pm2 <args>`` as ``user`` (None if pm2 or privileges are missing)."""
    binary = find_pm2(host, user.home)
    if binary is None:
        return None
    bindir = str(PurePosixPath(binary).parent)
    cmd = ["env", f"PM2_HOME={user.home.rstrip('/')}/.pm2",
           f"PATH={bindir}:/usr/local/bin:/usr/bin:/bin", binary, *args]
    if host.euid == user.uid:
        return cmd
    if host.euid == 0:
        return ["runuser", "-u", user.name, "--", *cmd]
    return None


def _daemon_alive(ctx: ScanContext, pm2_home: str) -> bool:
    pid = (ctx.host.read_text(f"{pm2_home}/pm2.pid") or "").strip()
    return pid.isdigit() and ctx.host.exists(f"/proc/{pid}")


def parse_jlist(output: str, user: UserEntry) -> list[Pm2App]:
    # pm2 may print warnings before the JSON array.
    m = re.search(r"^\s*\[\s*[{\]]", output, re.M)
    if m is None:
        raise ValueError("no JSON array in pm2 jlist output")
    data, _ = json.JSONDecoder().raw_decode(output[m.start():].lstrip())

    apps = []
    for item in data:
        env = item.get("pm2_env") or {}
        port = env.get("PORT") or (env.get("env") or {}).get("PORT")
        pid = item.get("pid") or None
        apps.append(
            Pm2App(
                name=str(item.get("name")),
                pm_id=int(item.get("pm_id", env.get("pm_id", -1))),
                user=user.name,
                home=user.home,
                pid=int(pid) if pid else None,
                status=PM2_STATUS.get(env.get("status", ""), "unknown"),
                cwd=env.get("pm_cwd"),
                script=env.get("pm_exec_path"),
                port=int(port) if str(port).isdigit() else None,
                pm2_home=f"{user.home.rstrip('/')}/.pm2",
                log_paths=[p for p in (env.get("pm_out_log_path"), env.get("pm_err_log_path"))
                           if isinstance(p, str) and p.startswith("/")],
            )
        )
    return apps


def collect(ctx: ScanContext, users: dict[int, UserEntry]) -> list[Pm2App]:
    apps: list[Pm2App] = []
    found_any = False
    for user in sorted(users.values(), key=lambda u: u.uid):
        pm2_home = f"{user.home.rstrip('/')}/.pm2"
        if not user.home or user.home == "/" or not ctx.host.is_dir(pm2_home):
            continue
        found_any = True
        if not _daemon_alive(ctx, pm2_home):
            ctx.warn(f"pm2: daemon not running for user {user.name}, skipped")
            continue
        if find_pm2(ctx.host, user.home) is None:
            ctx.warn(f"pm2: binary not found for user {user.name}")
            continue
        argv = pm2_command(ctx.host, user, ["jlist"])
        if argv is None:
            ctx.warn(f"pm2: cannot query user {user.name} without root")
            continue
        res = ctx.host.run(argv)
        if res is None or not res.ok:
            ctx.warn(f"pm2: `pm2 jlist` failed for user {user.name}")
            continue
        try:
            apps.extend(parse_jlist(res.stdout, user))
        except (ValueError, TypeError, KeyError) as exc:
            ctx.warn(f"pm2: cannot parse jlist for user {user.name}: {exc}")
    if not found_any:
        ctx.warn("pm2: no ~/.pm2 directory found, PM2 not in use")
    return apps
