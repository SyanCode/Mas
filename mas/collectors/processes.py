"""Process details from ``/proc/<pid>``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mas.collectors import ScanContext
from mas.host import Host

_CONTAINER_RE = re.compile(r"(?:docker-|/docker/)([0-9a-f]{64})")


@dataclass
class UserEntry:
    name: str
    uid: int
    home: str


@dataclass
class ProcessInfo:
    pid: int
    comm: str | None = None
    cmdline: list[str] = field(default_factory=list)
    cwd: str | None = None
    uid: int | None = None
    user: str | None = None
    ppid: int | None = None
    cgroup: str | None = None
    systemd_unit: str | None = None  # system unit only (system.slice)
    container_id: str | None = None
    pm2_app: str | None = None  # from the PM2 environment of the process

    @property
    def command(self) -> str:
        return " ".join(self.cmdline) if self.cmdline else (self.comm or "")


def load_users(host: Host) -> dict[int, UserEntry]:
    users: dict[int, UserEntry] = {}
    for line in (host.read_text("/etc/passwd") or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 6 and parts[2].isdigit():
            users[int(parts[2])] = UserEntry(parts[0], int(parts[2]), parts[5])
    return users


def parse_cgroup(text: str) -> tuple[str, str | None, str | None]:
    """Return ``(cgroup path, system unit, container id)``."""
    path = ""
    for line in text.splitlines():
        hierarchy, _, rest = line.partition(":")
        controllers, _, cg_path = rest.partition(":")
        if hierarchy == "0" and controllers == "":  # cgroup v2
            path = cg_path
            break
        if controllers == "name=systemd":
            path = cg_path
    m = _CONTAINER_RE.search(path)
    container = m.group(1) if m else None
    unit = None
    parts = [p for p in path.split("/") if p]
    if parts and parts[0] == "system.slice":
        unit = next((p for p in parts[1:] if p.endswith(".service")), None)
    return path, unit, container


def _parse_status(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def read_process(ctx: ScanContext, pid: int, users: dict[int, UserEntry]) -> ProcessInfo | None:
    host = ctx.host
    base = f"/proc/{pid}"
    status_text = host.read_text(f"{base}/status")
    if status_text is None:
        return None
    status = _parse_status(status_text)
    info = ProcessInfo(pid=pid, comm=status.get("Name"))
    if status.get("PPid", "").isdigit():
        info.ppid = int(status["PPid"])
    uid_field = status.get("Uid", "").split()
    if uid_field and uid_field[0].isdigit():
        info.uid = int(uid_field[0])
        entry = users.get(info.uid)
        info.user = entry.name if entry else str(info.uid)

    raw = host.read_bytes(f"{base}/cmdline") or b""
    info.cmdline = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    info.cwd = host.readlink(f"{base}/cwd")

    cgroup = host.read_text(f"{base}/cgroup")
    if cgroup:
        info.cgroup, info.systemd_unit, info.container_id = parse_cgroup(cgroup)

    environ = host.read_bytes(f"{base}/environ") or b""
    env = dict(
        item.decode("utf-8", "replace").split("=", 1)
        for item in environ.split(b"\0")
        if b"=" in item
    )
    if "pm_id" in env:
        info.pm2_app = env.get("name") or env["pm_id"]
    return info


class ProcessTable:
    """Lazy, cached access to processes."""

    def __init__(self, ctx: ScanContext, users: dict[int, UserEntry]) -> None:
        self.ctx = ctx
        self.users = users
        self._cache: dict[int, ProcessInfo | None] = {}

    def get(self, pid: int | None) -> ProcessInfo | None:
        if not pid:
            return None
        if pid not in self._cache:
            self._cache[pid] = read_process(self.ctx, pid, self.users)
        return self._cache[pid]

    def ancestors(self, pid: int, depth: int = 6) -> list[ProcessInfo]:
        """The process itself followed by its parents (stops at PID 1)."""
        out: list[ProcessInfo] = []
        current = self.get(pid)
        while current and len(out) < depth:
            out.append(current)
            if not current.ppid or current.ppid <= 1:
                break
            current = self.get(current.ppid)
        return out
