"""``mas self-update``: refresh the source, reinstall, rebuild the dashboard, restart."""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from mas.config import Config

KEEP_BACKUPS = 2
REPOSITORY = "https://github.com/SyanCode/Mas.git"


class UpdateError(Exception):
    pass


def _run(argv: list[str], cwd: Path | None = None, log=print) -> None:
    log(f"$ {' '.join(argv)}")
    proc = subprocess.run(argv, cwd=cwd, check=False)
    if proc.returncode != 0:
        raise UpdateError(f"command failed ({proc.returncode}): {' '.join(argv)}")


def _extract(tarball: Path, dest: Path) -> Path:
    with tarfile.open(tarball) as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python without extraction filters
            for member in tar.getmembers():
                if member.name.startswith(("/", "..")) or ".." in Path(member.name).parts:
                    raise UpdateError(f"unsafe path in archive: {member.name}")
            tar.extractall(dest)
    root = dest
    entries = [p for p in dest.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():  # archive with a top-level folder
        root = entries[0]
    if not (root / "pyproject.toml").is_file() or not (root / "mas").is_dir():
        raise UpdateError("this archive does not look like the Mas source tree")
    return root


def replace_source(config: Config, tarball: Path, log=print) -> None:
    src = Path(config.src_dir)
    with tempfile.TemporaryDirectory(dir=src.parent) as tmp:
        new_root = _extract(tarball, Path(tmp))
        if src.exists():
            backup = src.with_name(f"{src.name}.mas-bak-{time.strftime('%Y%m%d%H%M%S')}")
            src.rename(backup)
            log(f"previous source kept in {backup}")
        shutil.move(str(new_root), str(src))
    backups = sorted(src.parent.glob(f"{src.name}.mas-bak-*"))
    for old in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


def build_web(src: Path, log=print) -> None:
    web = src / "web"
    if not (web / "package.json").is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        raise UpdateError("npm not found: install nodejs/npm to build the dashboard")
    _run([npm, "ci", "--no-audit", "--no-fund"], cwd=web, log=log)
    _run([npm, "run", "build"], cwd=web, log=log)


DASHBOARD_VHOST = Path("/etc/nginx/sites-available/mas.conf")


def refresh_dashboard_vhost(src: Path, log=print) -> None:
    """Re-render the dashboard vhost from the new template (backup, nginx -t, restore on failure)."""
    template = src / "deploy" / "nginx-mas.conf"
    if not DASHBOARD_VHOST.is_file() or not template.is_file():
        return
    current = DASHBOARD_VHOST.read_text(encoding="utf-8")
    match = re.search(r"server_name\s+([^;\s]+);", current)
    if not match:
        return
    rendered = template.read_text(encoding="utf-8").replace("__DOMAIN__", match.group(1))
    if rendered == current:
        return
    backup = DASHBOARD_VHOST.with_name(f"{DASHBOARD_VHOST.name}.mas-bak-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(DASHBOARD_VHOST, backup)
    DASHBOARD_VHOST.write_text(rendered, encoding="utf-8")
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True, check=False)
    if test.returncode != 0:
        shutil.copy2(backup, DASHBOARD_VHOST)
        log(f"nginx -t failed with the new dashboard vhost, previous one restored:\n{test.stderr}")
        return
    _run(["systemctl", "reload", "nginx"], log=log)
    log(f"dashboard vhost updated (previous: {backup})")


def self_update(config: Config, tarball: Path | None = None, restart: bool = True, log=print) -> None:
    src = Path(config.src_dir)
    if tarball is not None:
        replace_source(config, tarball, log=log)
    elif (src / ".git").exists():
        _run(["git", "-C", str(src), "pull", "--ff-only"], log=log)
    else:
        # Installed from an archive: switch to a git checkout of the official repository.
        log(f"{src} is not a git checkout, cloning {REPOSITORY}")
        with tempfile.TemporaryDirectory(dir=src.parent) as tmp:
            clone = Path(tmp) / "src"
            _run(["git", "clone", "--quiet", REPOSITORY, str(clone)], log=log)
            if src.exists():
                backup = src.with_name(f"{src.name}.mas-bak-{time.strftime('%Y%m%d%H%M%S')}")
                src.rename(backup)
                log(f"previous source kept in {backup}")
            shutil.move(str(clone), str(src))

    pip = Path(config.venv_dir) / "bin" / "pip"
    _run([str(pip), "install", "--quiet", "--upgrade", str(src)], log=log)
    build_web(src, log=log)
    refresh_dashboard_vhost(src, log=log)
    if restart:
        _run(["systemctl", "restart", "mas"], log=log)
