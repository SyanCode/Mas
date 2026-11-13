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
TOKEN_START = re.compile(r"sk-ant-oat[0-9]*-")
TOKEN_PART = re.compile(r"[A-Za-z0-9_-]+")
CODE_RE = re.compile(r"^[A-Za-z0-9_#.:~-]{4,300}$")
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][A-Za-z0-9]|[@-Z\\-_])")
LIFETIME = 600


class LoginError(Exception):
    pass


def clean(raw: bytes) -> str:
    return ANSI_RE.sub("", raw.decode("utf-8", "replace")).replace("\r", "")


_SEQ = re.compile(r"\x1b\[([0-?]*)[ -/]*([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b.|([\r\n\b\t])|([^\x00-\x1f\x7f])", re.S)
WIDTH = 1000


def render(raw: bytes) -> str:
    """What the terminal screen shows after this output.

    The login tool is a full-screen interface: it redraws only what changed, moving the cursor over the cells that are
    already right (it writes ``sk-ant-``, jumps to column 10 and goes on, because the "o" was drawn earlier). The text of
    the stream alone is therefore missing letters; replaying it on a grid, like a terminal does, gives the real text."""
    rows: dict[int, list[str]] = {}
    row = col = 0

    def put(ch: str) -> None:
        nonlocal row, col
        if col >= WIDTH:
            row, col = row + 1, 0
        line = rows.setdefault(row, [])
        if len(line) <= col:
            line.extend(" " * (col + 1 - len(line)))
        line[col] = ch
        col += 1

    for m in _SEQ.finditer(raw.decode("utf-8", "replace")):
        params, final, ctrl, char = m.groups()
        if char:
            put(char)
        elif ctrl:
            if ctrl == "\r":
                col = 0
            elif ctrl == "\n":
                row += 1
            elif ctrl == "\b":
                col = max(0, col - 1)
            else:
                col = (col // 8 + 1) * 8
        elif final:
            nums = [int(x) if x.isdigit() else 0 for x in params.lstrip("?").split(";")] if params else []
            n = nums[0] if nums and nums[0] else 1
            if final == "G":
                col = n - 1
            elif final == "C":
                col += n
            elif final == "D":
                col = max(0, col - n)
            elif final == "A":
                row = max(0, row - n)
            elif final == "B":
                row += n
            elif final in "Hf":
                row = (nums[0] or 1) - 1 if nums else 0
                col = (nums[1] or 1) - 1 if len(nums) > 1 else 0
            elif final == "K":
                line = rows.setdefault(row, [])
                mode = nums[0] if nums else 0
                if mode == 0:
                    del line[col:]
                elif mode == 1:
                    line[:col + 1] = [" "] * min(len(line), col + 1)
                else:
                    rows[row] = []
            elif final == "J":
                mode = nums[0] if nums else 0
                if mode in (2, 3):
                    rows.clear()
                elif mode == 0:
                    for r in [r for r in rows if r > row]:
                        del rows[r]
                    del rows.setdefault(row, [])[col:]
    return "\n".join("".join(rows.get(r, [])).rstrip() for r in range(min(rows, default=0), max(rows, default=-1) + 1))


def extract_token(text: str, final: bool) -> str | None:
    """The token printed by the CLI, even when the terminal broke it over several lines.

    It ends at the first line that is not made of token characters (a blank line, the "store it securely" advice). While
    the output is still arriving a last, unfinished line could be cut in the middle: it counts only once ``final``."""
    m = TOKEN_START.search(text)
    if not m:
        return flat_token(text, final)
    lines = text[m.start():].split("\n")
    first = lines[0].split()
    token = first[0] if first else ""
    ended = len(first) > 1  # text after the token on its line: it ended there
    for raw in lines[1:]:
        if ended:
            break
        line = raw.strip()
        if not TOKEN_PART.fullmatch(line):
            ended = True  # a blank line, or the advice that follows
            break
        token += line
    if (ended or final) and len(token) >= 40 and "Store" not in token[40:]:
        return token
    return flat_token(text, final)


def flat_token(text: str, final: bool) -> str | None:
    """Last resort: the text with every space and line break removed, in case the terminal cut the token (or its prefix)
    somewhere unexpected. The advice printed after the token is cut off where it starts."""
    flat = re.sub(r"[\s\u2500-\u257f|]+", "", text)  # spaces, line breaks, box borders
    m = TOKEN_START.search(flat)
    if not m:
        return None
    run = re.match(r"[A-Za-z0-9_-]+", flat[m.start():])
    token = run.group(0) if run else ""
    ended = m.start() + len(token) < len(flat)  # something else follows it
    cut = token.find("Store", 40)
    if cut > 0:
        token, ended = token[:cut], True
    return token if (ended or final) and len(token) >= 60 else None


def raw_window(raw: bytes) -> str:
    """The raw bytes just around where the token starts (its first characters only): shows terminal codes, if any."""
    i = raw.find(b"sk-ant-")
    if i < 0:
        return ""
    return repr(raw[max(0, i - 40):i + 14])[2:-1]


def shape(text: str) -> str:
    """The output with its long secret-looking runs masked: enough to see how it is laid out, nothing sensitive."""
    masked = re.sub(r"[A-Za-z0-9_-]{16,}", lambda m: (m.group(0)[:13] if m.group(0).startswith("sk-ant") else m.group(0)[:3]) + f"<{len(m.group(0))}>", text)
    return masked.replace("\n", "⏎").strip()[-500:]


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

    def _read(self, s: dict, until, timeout: float, view=clean) -> str | None:
        """Read the CLI's output until ``until(text)`` returns something, the CLI exits or the time is up."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = until(view(s["buffer"]), False)
            if found:
                return found
            ready, _, _ = select.select([s["fd"]], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(s["fd"], 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    return until(view(s["buffer"]), True)
                s["buffer"] += chunk
        return until(view(s["buffer"]), True)

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
            url = self._read(self._session, lambda text, final: (m := URL_RE.search(text)) and m.group(0), 25)
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
            find = extract_token
            # The CLI's input line takes a quick burst for a paste: the code first, then Return on its own, a moment later
            # (and once more if nothing happened: some versions drop the first one).
            os.write(s["fd"], code.encode())
            time.sleep(0.6)
            os.write(s["fd"], b"\r")
            token = self._read(s, find, 12, render)
            if not token and s["proc"].poll() is None:
                os.write(s["fd"], b"\r")
                token = self._read(s, find, 30, render)
            tail = shape(render(s["buffer"]))
            window = raw_window(s["buffer"])
            if window:
                tail += f" — raw: {window}"
            self._close()
            if not token:
                raise LoginError("Claude did not give a token after the code" + (f" — what the tool printed: {tail}" if tail else "") + " — start again")
            return token
