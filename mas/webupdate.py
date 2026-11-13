"""Update Mas from the dashboard: is there a newer version, and run ``mas self-update`` for the administrator.

The update replaces and restarts the process that serves the dashboard, so it runs in its own transient systemd
unit (``systemd-run``), outside this service, and writes its output to a log the dashboard reads meanwhile."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from mas import __version__
from mas.banner import version_from_source
from mas.config import Config
from mas.db import Database
from mas.host import Host
from mas.selfupdate import REPOSITORY, local_head

UNIT = "mas-self-update"
CHECK_TTL = 3600      # between two looks at GitHub
FORCE_MIN = 30        # a manual check cannot hammer the API
SETTING = "update_check"
STARTED = "update_started"
MAX_COMMITS = 30
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SLUG = REPOSITORY.removeprefix("https://github.com/").removesuffix(".git")


class UpdateError(Exception):
    pass


def _get(url: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "mas-update-check"})
    with urllib.request.urlopen(req, timeout=6) as res:
        body = res.read(2_000_000)
    return body


def github_fetch(sha: str, branch: str) -> dict:
    """What the official repository has that this checkout does not: {behind, commits, version, unknown}."""
    out: dict = {"behind": 0, "commits": [], "version": None, "unknown": False}
    try:
        data = json.loads(_get(f"https://api.github.com/repos/{SLUG}/compare/{sha}...{branch}"))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        out.update(behind=1, unknown=True)  # the commit is unknown to GitHub (history rewritten): an update is needed
    else:
        if data.get("status") in ("ahead", "diverged"):
            out["behind"] = int(data.get("ahead_by", 0))
            out["commits"] = [{"sha": c["sha"][:7], "message": (c["commit"]["message"] or "").split("\n")[0][:140],
                               "date": c["commit"]["author"]["date"]} for c in data.get("commits", [])][::-1][:MAX_COMMITS]
    if out["behind"]:
        try:
            raw = _get(f"https://api.github.com/repos/{SLUG}/contents/mas/__init__.py?ref={branch}",
                       "application/vnd.github.raw+json").decode("utf-8", "replace")
            m = re.search(r'__version__\s*=\s*"([^"]+)"', raw)
            out["version"] = m.group(1) if m else None
        except (OSError, ValueError):
            pass
    return out


class Updater:
    def __init__(self, host: Host, config: Config, db: Database, fetch: Callable[[str, str], dict] = github_fetch) -> None:
        self.host, self.config, self.db, self.fetch = host, config, db, fetch

    @property
    def log_path(self) -> Path:
        return Path(self.config.db_path).parent / "update.log"

    def _unit_state(self) -> str:
        res = self.host.run(["systemctl", "is-active", UNIT], timeout=10)
        return (res.stdout if res else "").strip()

    def status(self, force: bool = False, now: float | None = None) -> dict:
        now = now or time.time()
        head = local_head(Path(self.config.src_dir))
        running = __version__
        on_disk = version_from_source(self.config.src_dir)
        base = {"version": running, "on_disk": on_disk, "restart_pending": bool(on_disk and on_disk != running),
                "branch": head[0] if head else None, "sha": head[1][:7] if head else None,
                "busy": self._unit_state() in ("active", "activating"), "behind": 0, "commits": [], "latest_version": None,
                "unknown": False, "checked_at": None, "error": None, "can_update": True}
        if head is None:
            return {**base, "can_update": True, "error": None, "note": "not a git checkout: the update clones the official repository"}
        cached = json.loads(self.db.get_setting(SETTING) or "null")
        fresh = cached and cached.get("sha") == head[1] and now - cached.get("checked_at", 0) < (FORCE_MIN if force else CHECK_TTL)
        if not fresh:
            try:
                got = self.fetch(head[1], head[0])
                cached = {"sha": head[1], "checked_at": now, "behind": got["behind"], "commits": got["commits"],
                          "latest_version": got.get("version"), "unknown": got.get("unknown", False)}
                self.db.set_setting(SETTING, json.dumps(cached))
            except (OSError, ValueError) as exc:  # offline or rate-limited: say so, keep what we knew
                return {**base, **({k: cached[k] for k in ("behind", "commits", "latest_version", "unknown", "checked_at")} if cached and cached.get("sha") == head[1] else {}),
                        "error": f"could not reach GitHub: {exc}"[:200]}
        return {**base, **{k: cached[k] for k in ("behind", "commits", "latest_version", "unknown", "checked_at")}}

    def start(self, actor: str) -> dict:
        if self._unit_state() in ("active", "activating"):
            raise UpdateError("an update is already running")
        mas = Path(self.config.venv_dir) / "bin" / "mas"
        log = self.log_path
        size = log.stat().st_size if log.exists() else 0
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n=== update started by {actor} at {time.strftime('%Y-%m-%d %H:%M:%S')} (from {__version__}) ===\n")
        res = self.host.run(["systemd-run", f"--unit={UNIT}", "--collect", "--no-block",
                             f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
                             "--setenv=MAS_NO_UPDATE_CHECK=1", str(mas), "self-update"], timeout=20)
        if res is None or res.returncode != 0:
            raise UpdateError(f"could not start the update: {(res.stderr if res else 'systemd-run not found').strip()[:300]}")
        self.db.set_setting(STARTED, json.dumps({"at": time.time(), "offset": size, "from": __version__, "by": actor}))
        self.db.set_setting(SETTING, None)  # the next look is a fresh one
        return {"started": True}

    def progress(self) -> dict:
        info = json.loads(self.db.get_setting(STARTED) or "null")
        if not info:
            return {"state": "idle", "log": ""}
        try:
            raw = self.log_path.read_bytes()[info["offset"]:].decode("utf-8", "replace")
        except OSError:
            raw = ""
        text = ANSI.sub("", raw)
        lines = [l for l in text.replace("\r", "\n").split("\n") if l.strip()]
        if self._unit_state() in ("active", "activating"):
            state = "running"
        elif "Mas updated" in text:
            state = "done"
        elif lines:
            state = "failed"
        else:
            state = "running" if time.time() - info["at"] < 20 else "failed"  # nothing written yet: starting up
        return {"state": state, "log": "\n".join(lines[-40:]), "from": info["from"], "started_at": info["at"]}
