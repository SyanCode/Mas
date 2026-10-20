"""Listening sockets from ``ss`` (TCP and unix stream sockets)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mas.collectors import ScanContext

_USERS_RE = re.compile(r'\("([^"]*)",pid=(\d+),fd=\d+\)')


@dataclass
class ListeningSocket:
    port: int | None  # None for unix sockets
    address: str
    process: str | None = None
    pids: list[int] = field(default_factory=list)

    @property
    def pid(self) -> int | None:
        # ss lists workers before the master; the lowest pid is usually the parent.
        return self.pids[0] if self.pids else None

    @property
    def is_loopback(self) -> bool:
        return self.address.startswith("127.") or self.address in ("::1", "localhost")


def _split_local(local: str) -> tuple[str, int | None]:
    addr, _, port = local.rpartition(":")
    addr = addr.strip("[]")
    addr = addr.split("%", 1)[0]  # 127.0.0.53%lo
    if addr.startswith("::ffff:"):
        addr = addr[len("::ffff:"):]
    return addr, int(port) if port.isdigit() else None


def _users(text: str) -> tuple[str | None, list[int]]:
    """Return the owning process name and its pids, lowest (master) first.

    With socket activation systemd (PID 1) holds the socket too; the real
    service is the other process, so PID 1 only counts when it is alone.
    """
    owners = {int(pid): name for name, pid in _USERS_RE.findall(text)}
    if len(owners) > 1:
        owners.pop(1, None)
    if not owners:
        return None, []
    pids = sorted(owners)
    return owners[pids[0]], pids


def parse_ss_tcp(output: str) -> list[ListeningSocket]:
    """Parse ``ss -tlnpH`` output."""
    merged: dict[tuple[int, tuple[int, ...] | str], ListeningSocket] = {}
    for line in output.splitlines():
        cols = line.split(None, 5)
        if len(cols) < 5 or cols[0] != "LISTEN":
            continue
        addr, port = _split_local(cols[3])
        if port is None:
            continue
        process, pids = _users(cols[5] if len(cols) > 5 else "")
        key = (port, tuple(pids) if pids else addr)
        if key in merged:
            # Same service on IPv4 and IPv6: keep the most specific address.
            if merged[key].address in ("0.0.0.0", "*", "::") and addr not in ("0.0.0.0", "*", "::"):
                merged[key].address = addr
            continue
        merged[key] = ListeningSocket(port, addr, process, pids)
    return sorted(merged.values(), key=lambda s: (s.port or 0, s.address))


def parse_ss_unix(output: str) -> list[ListeningSocket]:
    """Parse ``ss -xlnpH`` output (path-bound unix stream sockets only)."""
    out: list[ListeningSocket] = []
    for line in output.splitlines():
        cols = line.split()
        if len(cols) < 5 or cols[0] != "u_str":
            continue
        path = next((c for c in cols[4:] if c.startswith("/")), None)
        if not path:
            continue
        process, pids = _users(line)
        out.append(ListeningSocket(None, path, process, pids))
    return out


def collect(ctx: ScanContext) -> list[ListeningSocket]:
    res = ctx.host.run(["ss", "-tlnpH"])
    if res is None or not res.ok:
        ctx.warn("ports: `ss -tlnpH` unavailable, listening ports not detected")
        return []
    sockets = parse_ss_tcp(res.stdout)
    if sockets and not any(s.pids for s in sockets):
        ctx.warn("ports: no process info from ss (run as root to see PIDs)")
    return sockets


def collect_unix(ctx: ScanContext) -> list[ListeningSocket]:
    res = ctx.host.run(["ss", "-xlnpH"])
    if res is None or not res.ok:
        return []
    return parse_ss_unix(res.stdout)
