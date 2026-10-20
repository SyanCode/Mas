"""Read the logs of a service, according to its manager. Read-only."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from mas.actions import CONTAINER_RE, PM2_NAME_RE, UNIT_RE
from mas.collectors.pm2 import pm2_command
from mas.collectors.processes import load_users
from mas.host import Host
from mas.models import Manager, Service

MAX_LINES = 2000


@dataclass
class LogSource:
    name: str
    lines: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "lines": self.lines, "error": self.error}


def _journal(host: Host, unit: str | None, lines: int) -> LogSource:
    if not unit or not UNIT_RE.fullmatch(unit):
        return LogSource("journal", error="no systemd unit")
    res = host.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"])
    if res is None or not res.ok:
        return LogSource(f"journalctl -u {unit}", error="journalctl unavailable")
    return LogSource(f"journalctl -u {unit}", res.stdout.splitlines()[-lines:])


def _files(host: Host, paths: list[str], lines: int) -> list[LogSource]:
    out = []
    for path in paths:
        content = host.tail(path, lines)
        out.append(LogSource(path, content or [], None if content is not None else "cannot read file"))
    return out


def demux_docker(data: bytes) -> list[str]:
    """Docker multiplexes stdout/stderr with 8-byte frame headers (non-TTY containers)."""
    if len(data) < 8 or data[0] not in (0, 1, 2) or data[1:4] != b"\0\0\0":
        return data.decode("utf-8", "replace").splitlines()
    chunks, pos = [], 0
    while pos + 8 <= len(data):
        (size,) = struct.unpack(">I", data[pos + 4 : pos + 8])
        chunks.append(data[pos + 8 : pos + 8 + size])
        pos += 8 + size
    return b"".join(chunks).decode("utf-8", "replace").splitlines()


def get_logs(host: Host, svc: Service, lines: int = 200) -> list[LogSource]:
    lines = max(1, min(int(lines), MAX_LINES))

    if svc.manager in (Manager.SYSTEMD, Manager.PHP_FPM):
        sources = [_journal(host, svc.unit, lines)]
        if svc.manager == Manager.PHP_FPM:
            sources += _files(host, svc.log_paths, lines)
        return sources

    if svc.manager == Manager.PM2:
        if svc.log_paths:
            return _files(host, svc.log_paths, lines)
        user = next((u for u in load_users(host).values() if u.name == svc.user), None)
        if not user or not svc.unit or not PM2_NAME_RE.fullmatch(svc.unit):
            return [LogSource("pm2", error="pm2 app unknown")]
        argv = pm2_command(host, user, ["logs", svc.unit, "--nostream", "--lines", str(lines)])
        res = host.run(argv) if argv else None
        if res is None or not res.ok:
            return [LogSource("pm2 logs", error="pm2 logs unavailable")]
        return [LogSource(f"pm2 logs {svc.unit}", res.stdout.splitlines()[-lines:])]

    if svc.manager == Manager.DOCKER:
        if not svc.container_id or not CONTAINER_RE.fullmatch(svc.container_id):
            return [LogSource("docker", error="no container id")]
        api = f"/containers/{svc.container_id}/logs?stdout=1&stderr=1&timestamps=1&tail={lines}"
        try:
            data = host.docker_request("GET", api)
        except OSError as exc:
            return [LogSource("docker logs", error=str(exc))]
        return [LogSource(f"docker logs {svc.unit or svc.container_id[:12]}", demux_docker(data)[-lines:])]

    if svc.manager == Manager.NGINX_STATIC:
        return _files(host, svc.log_paths, lines)

    return [LogSource("logs", error="no log source for an unmanaged process")]
