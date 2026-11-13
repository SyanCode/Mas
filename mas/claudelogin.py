"""Connect Claude without hunting for a token: Mas drives the official Claude Code login on this server.

``claude setup-token`` prints a link; the administrator opens it, signs in to Claude and gets a short code; Mas types
that code into the CLI and reads back the long-lived token it prints, which becomes an AI account (mas.ai). The CLI runs
in a throw-away home folder, one login at a time, for at most ten minutes. Mas only reads the CLI's text: if a future
version of the CLI words it differently the login says so, and the token can still be pasted by hand.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

from mas.host import Host

CANDIDATES = ("/usr/local/bin/claude", "/usr/bin/claude", "/root/.local/bin/claude", "/root/.npm-global/bin/claude")
INSTALL = ["npm", "install", "-g", "@anthropic-ai/claude-code"]
URL_RE = re.compile(r"https://[^\s\x1b\"'<>]*(?:claude\.(?:ai|com)|anthropic\.com)[^\s\x1b\"'<>]*")
TOKEN_RE = re.compile(r"sk-ant-oat[0-9]*-[A-Za-z0-9_-]{20,}")
CODE_RE = re.compile(r"^[A-Za-z0-9_#.:~-]{4,300}$")
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
LIFETIME = 600


class LoginError(Exception):
    pass


def clean(raw: bytes) -> str:
    return ANSI_RE.sub("", raw.decode("utf-8", "replace")).replace("\r", "")


def binary(host: Host, override: str | None = None) -> str | None:
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("claude")
    return found or next((p for p in CANDIDATES if host.exists(p)), None)


class ClaudeLogin:
    def __init__(self, host: Host, home: str, override: str | None = None) -> None:
        self.host, self.home, self.override = host, home, override
        self._lock = threading.Lock()
        self._session: dict | None = None  # {"proc", "fd", "buffer", "started"}

    # -- the CLI -----------------------------------------------------------------------------------

    def status(self) -> dict:
        path = binary(self.host, self.override)
        return {"installed": bool(path), "npm": bool(shutil.which("npm") or self.host.exists("/usr/bin/npm")),
                "pending": self._alive()}

    def install(self) -> str:
        """npm install -g, outside Mas's sandbox (it cannot write /usr): through systemd-run."""
        if not (shutil.which("npm") or self.host.exists("/usr/bin/npm")):
            raise LoginError("Node.js is needed to install Claude Code: apt install nodejs npm")
        res = self.host.run(["systemd-run", "--quiet", "--wait", "--collect", "--pipe", *INSTALL], timeout=420)
        if res is None:
            raise LoginError("could not run the installation (systemd-run or npm not found)")
        if res.returncode != 0:
            raise LoginError("the installation failed: " + (res.stderr or res.stdout).strip()[-400:])
        return (res.stdout or "").strip()[-400:]

    # -- one login at a time -----------------------------------------------------------------------

    def _alive(self) -> bool:
        s = self._session
        return bool(s and s["proc"].poll() is None and time.monotonic() - s["started"] < LIFETIME)

    def cancel(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        s, self._session = self._session, None
        if not s:
            return
        try:
            os.killpg(s["proc"].pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            s["proc"].wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.close(s["fd"])
        except OSError:
            pass

    def _read(self, s: dict, until, timeout: float) -> str | None:
        """Read the CLI's output until ``until(text)`` returns something, the CLI exits or the time is up."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = until(clean(s["buffer"]))
            if found:
                return found
            ready, _, _ = select.select([s["fd"]], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(s["fd"], 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    return until(clean(s["buffer"]))
                s["buffer"] += chunk
        return until(clean(s["buffer"]))

    def start(self) -> str:
        """Start the CLI login and return the link to open."""
        path = binary(self.host, self.override)
        if not path:
            raise LoginError("Claude Code is not installed on this server")
        with self._lock:
            self._close()
            Path(self.home).mkdir(parents=True, exist_ok=True)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))  # wide: a long link never wraps
            env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": self.home, "TERM": "xterm",
                   "BROWSER": "true", "NO_COLOR": "1", "CI": ""}
            proc = subprocess.Popen([path, "setup-token"], stdin=slave, stdout=slave, stderr=slave, env=env, cwd=self.home,
                                    start_new_session=True, close_fds=True)
            os.close(slave)
            self._session = {"proc": proc, "fd": master, "buffer": b"", "started": time.monotonic()}
            url = self._read(self._session, lambda text: (m := URL_RE.search(text)) and m.group(0), 25)
            if not url:
                tail = clean(self._session["buffer"]).strip()[-300:]
                self._close()
                raise LoginError("Claude Code did not give a login link" + (f": {tail}" if tail else ""))
            return url

    def finish(self, code: str) -> str:
        """Type the code into the CLI and return the token it prints."""
        code = code.strip()
        if not CODE_RE.fullmatch(code):
            raise LoginError("this does not look like the code Claude shows after signing in")
        with self._lock:
            s = self._session
            if not s or not self._alive():
                self._close()
                raise LoginError("no login is waiting: start again")
            os.write(s["fd"], code.encode() + b"\r")
            token = self._read(s, lambda text: (m := TOKEN_RE.search(text)) and m.group(0), 40)
            tail = clean(s["buffer"]).strip()[-200:]
            self._close()
            if not token:
                raise LoginError("Claude did not accept the code" + (f" ({tail})" if tail else "") + ": start again")
            return token
