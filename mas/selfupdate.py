"""``mas self-update``: refresh the source, reinstall, rebuild the dashboard, restart."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
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


CHECK_TTL = 6 * 3600
CHECK_TIMEOUT = 2


def local_head(src: Path) -> tuple[str, str] | None:
    """(branch, commit) of the source checkout, read from .git without running git
    (git refuses a root-owned repository to other users)."""
    git = src / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: refs/heads/"):
            return None  # detached
        ref = head[len("ref: "):]
        loose = git / ref
        if loose.is_file():
            return ref[len("refs/heads/"):], loose.read_text(encoding="utf-8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref:
                return ref[len("refs/heads/"):], sha
    except OSError:
        return None
    return None


def _commits_behind(sha: str, branch: str) -> int:
    slug = REPOSITORY.removeprefix("https://github.com/").removesuffix(".git")
    req = urllib.request.Request(f"https://api.github.com/repos/{slug}/compare/{sha}...{branch}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "mas-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # commit unknown to GitHub (history rewritten): an update is needed
            return 1
        raise
    return int(data.get("ahead_by", 0)) if data.get("status") in ("ahead", "diverged") else 0


def update_available(config: Config, now: float | None = None, fetch=_commits_behind) -> int:
    """Commits the installed Mas is behind the official repository (0 if unknown).

    Cached for a few hours next to the database; skipped when the cache cannot be written
    (not root), so a plain ``mas`` never waits on the network more than once in a while."""
    head = local_head(Path(config.src_dir))
    if head is None:
        return 0
    branch, sha = head
    cache = Path(config.db_path).parent / "update-check.json"
    now = now or time.time()
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("sha") == sha and now - cached.get("checked_at", 0) < CHECK_TTL:
            return int(cached.get("behind", 0))
    except (OSError, ValueError):
        pass
    if not os.access(cache.parent, os.W_OK):
        return 0
    try:
        behind = fetch(sha, branch)
    except (OSError, ValueError):
        behind = 0  # offline or rate-limited: retried after the TTL
    try:
        cache.write_text(json.dumps({"sha": sha, "checked_at": now, "behind": behind}), encoding="utf-8")
    except OSError:
        pass
    return behind


def _git_output(src: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def update_checkout(src: Path, log=print) -> None:
    """Move the checkout to the published branch, even after a force-push; local edits stop it."""
    branch = _git_output(src, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise UpdateError(f"{src} is on a detached HEAD: check out a branch first")
    changed = _git_output(src, "status", "--porcelain", "--untracked-files=no")
    if changed:
        raise UpdateError(f"{src} has local changes, nothing was updated:\n{changed}")
    _run(["git", "-C", str(src), "fetch", "--prune", "origin"], log=log)
    _run(["git", "-C", str(src), "reset", "--hard", f"origin/{branch}"], log=log)


def self_update(config: Config, tarball: Path | None = None, restart: bool = True, log=print) -> None:
    src = Path(config.src_dir)
    if tarball is not None:
        replace_source(config, tarball, log=log)
    elif (src / ".git").exists():
        update_checkout(src, log=log)
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
