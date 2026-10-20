"""Deterministic service type detection.

Rules are evaluated in order; the first match wins. A manual label stored in
the database always takes precedence (applied by the caller).
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from mas.host import Host
from mas.models import ServiceType

PYTHON_RUNNERS = {"gunicorn", "uvicorn", "hypercorn", "daphne", "waitress-serve"}
NODE_RUNNERS = {"node", "nodejs", "bun", "deno"}


def _package_deps(host: Host, path: str) -> dict:
    try:
        pkg = json.loads(host.read_text(f"{path}/package.json") or "{}")
    except ValueError:
        return {}
    if not isinstance(pkg, dict):
        return {}
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(key), dict):
            deps.update(pkg[key])
    return deps


def _from_cmdline(cmdline: list[str]) -> ServiceType | None:
    joined = " ".join(cmdline)
    if "next-server" in joined or "/next/dist/" in joined or "next start" in joined:
        return ServiceType.NEXTJS
    exe = PurePosixPath(cmdline[0].split()[0]).name if cmdline and cmdline[0] else ""
    if exe.startswith("python") or exe in PYTHON_RUNNERS:
        return ServiceType.PYTHON
    if exe in NODE_RUNNERS:
        return ServiceType.NODE
    return None


def classify(files: set[str], package_deps: dict, *, fastcgi: bool = False) -> ServiceType | None:
    """The file rules, on a set of names (a folder or a GitHub repository root)."""
    if fastcgi or any(f.endswith(".php") for f in files) or "composer.json" in files:
        return ServiceType.PHP
    if "package.json" in files:
        return ServiceType.NEXTJS if "next" in package_deps else ServiceType.NODE
    if "requirements.txt" in files or "pyproject.toml" in files:
        return ServiceType.PYTHON
    return None


def detect_type(
    host: Host,
    path: str | None,
    *,
    fastcgi: bool = False,
    proxy: bool = False,
    static_root: bool = False,
    cmdline: list[str] | None = None,
) -> ServiceType:
    files = set(host.listdir(path)) if path else set()
    deps = _package_deps(host, path) if "package.json" in files else {}

    found = classify(files, deps, fastcgi=fastcgi)
    if found:
        return found
    if static_root and not proxy:
        return ServiceType.STATIC
    # Last resort: what the process itself looks like.
    if cmdline:
        hinted = _from_cmdline(cmdline)
        if hinted:
            return hinted
    return ServiceType.UNKNOWN
