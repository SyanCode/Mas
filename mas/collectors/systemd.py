"""Running systemd services, minus the OS ones (see ``Config.excluded_units``)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from mas.collectors import ScanContext

SHOW_PROPERTIES = "Id,MainPID,WorkingDirectory,User,ExecStart,FragmentPath,ActiveState,SubState,Description"
_ARGV_RE = re.compile(r"argv\[\]=(.*?) ;")


@dataclass
class SystemdUnit:
    name: str
    description: str = ""
    active: str = "active"
    sub: str = "running"
    main_pid: int | None = None
    working_directory: str | None = None
    user: str | None = None
    exec_start: str | None = None
    fragment_path: str | None = None


def parse_list_json(output: str) -> list[SystemdUnit]:
    data = json.loads(output)
    return [
        SystemdUnit(
            name=u["unit"],
            description=u.get("description", ""),
            active=u.get("active", ""),
            sub=u.get("sub", ""),
        )
        for u in data
        if u.get("unit", "").endswith(".service")
    ]


def parse_list_text(output: str) -> list[SystemdUnit]:
    units = []
    for line in output.splitlines():
        cols = line.split(None, 4)
        if len(cols) >= 4 and cols[0].endswith(".service"):
            units.append(
                SystemdUnit(name=cols[0], active=cols[2], sub=cols[3],
                            description=cols[4] if len(cols) > 4 else "")
            )
    return units


def parse_show(output: str) -> dict[str, dict[str, str]]:
    """Parse ``systemctl show`` output for several units (blank-line separated)."""
    result: dict[str, dict[str, str]] = {}
    for block in re.split(r"\n\s*\n", output.strip()):
        props = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        if props.get("Id"):
            result[props["Id"]] = props
    return result


def _list_units(ctx: ScanContext) -> list[SystemdUnit] | None:
    base = ["systemctl", "list-units", "--type=service", "--state=running",
            "--no-pager", "--plain", "--no-legend"]
    res = ctx.host.run(base + ["--output=json"])
    if res is not None and res.ok:
        try:
            return parse_list_json(res.stdout)
        except (ValueError, KeyError, TypeError):
            pass  # old systemd: fall back to text output
    res = ctx.host.run(base)
    if res is None or not res.ok:
        return None
    return parse_list_text(res.stdout)


def collect(ctx: ScanContext) -> list[SystemdUnit]:
    units = _list_units(ctx)
    if units is None:
        ctx.warn("systemd: systemctl unavailable, systemd services not detected")
        return []
    units = [u for u in units if not ctx.config.unit_excluded(u.name)]
    if not units:
        return []

    res = ctx.host.run(["systemctl", "show", "--no-pager", "-p", SHOW_PROPERTIES,
                        *[u.name for u in units]])
    details = parse_show(res.stdout) if res is not None and res.ok else {}
    for unit in units:
        props = details.get(unit.name, {})
        pid = props.get("MainPID", "")
        unit.main_pid = int(pid) if pid.isdigit() and pid != "0" else None
        unit.working_directory = props.get("WorkingDirectory") or None
        unit.user = props.get("User") or None
        unit.fragment_path = props.get("FragmentPath") or None
        m = _ARGV_RE.search(props.get("ExecStart", ""))
        unit.exec_start = m.group(1) if m else None
    return units
