"""Access to the host system.

Every collector reads the machine through a ``Host`` so the whole detection
pipeline can run against a fixture directory (tests, non-Linux dev machines).
Paths handled here are always *virtual* POSIX paths ("/etc/nginx/...").

Nothing in this module writes to the system. Commands are executed with an
argument list, never through a shell.
"""

from __future__ import annotations

import fnmatch
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

DOCKER_SOCKET = "/var/run/docker.sock"
MAX_READ_BYTES = 4 * 1024 * 1024


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class Host:
    """The real machine. ``root`` is "/" except in tests."""

    def __init__(self, root: str | Path = "/", timeout: float = 10.0) -> None:
        self.root = Path(root)
        self.timeout = timeout

    # -- paths -----------------------------------------------------------

    def real(self, path: str) -> Path:
        p = PurePosixPath(path)
        parts = p.parts[1:] if p.is_absolute() else p.parts
        return self.root.joinpath(*parts)

    def virtual(self, real_path: Path) -> str:
        rel = Path(real_path).relative_to(self.root)
        return "/" + rel.as_posix() if rel.parts else "/"

    def exists(self, path: str) -> bool:
        try:
            return self.real(path).exists()
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        try:
            return self.real(path).is_dir()
        except OSError:
            return False

    def is_file(self, path: str) -> bool:
        try:
            return self.real(path).is_file()
        except OSError:
            return False

    def read_bytes(self, path: str, max_bytes: int = MAX_READ_BYTES) -> bytes | None:
        try:
            with open(self.real(path), "rb") as fh:
                return fh.read(max_bytes)
        except OSError:
            return None

    def file_id(self, path: str) -> tuple[int, int] | None:
        """(inode, size): a log that was rotated has another inode."""
        try:
            st = os.stat(self.real(path))
        except OSError:
            return None
        return st.st_ino, st.st_size

    def read_from(self, path: str, offset: int, max_bytes: int) -> bytes:
        try:
            with open(self.real(path), "rb") as fh:
                fh.seek(offset)
                return fh.read(max_bytes)
        except OSError:
            return b""

    def read_tail(self, path: str, max_bytes: int) -> bytes:
        """The last ``max_bytes`` of a file (a log), from its first complete line."""
        try:
            with open(self.real(path), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                data = fh.read(max_bytes)
        except OSError:
            return b""
        return data.split(b"\n", 1)[1] if size > max_bytes and b"\n" in data else data

    def read_text(self, path: str, max_bytes: int = MAX_READ_BYTES) -> str | None:
        data = self.read_bytes(path, max_bytes)
        return None if data is None else data.decode("utf-8", errors="replace")

    def listdir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(self.real(path)))
        except OSError:
            return []

    def glob(self, pattern: str) -> list[str]:
        """Expand a virtual glob pattern, returning sorted virtual paths."""
        p = PurePosixPath(pattern)
        parts = p.parts[1:] if p.is_absolute() else p.parts
        if not parts:
            return []
        try:
            matches = self.root.glob("/".join(parts))
            return sorted(self.virtual(m) for m in matches)
        except (OSError, ValueError):
            return []

    def readlink(self, path: str) -> str | None:
        try:
            return os.readlink(self.real(path))
        except OSError:
            return None

    def tail(self, path: str, lines: int, chunk: int = 64 * 1024) -> list[str] | None:
        """Last ``lines`` lines of a file, reading backwards from the end."""
        try:
            with open(self.real(path), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                end = pos = fh.tell()
                data = b""
                while pos > 0 and data.count(b"\n") <= lines and end - pos < MAX_READ_BYTES:
                    step = min(chunk, pos)
                    pos -= step
                    fh.seek(pos)
                    data = fh.read(step) + data
        except OSError:
            return None
        return data.decode("utf-8", "replace").splitlines()[-lines:]

    def is_symlink(self, path: str) -> bool:
        try:
            return self.real(path).is_symlink()
        except OSError:
            return False

    def owner_uid(self, path: str) -> int | None:
        try:
            return self.real(path).stat().st_uid
        except OSError:
            return None

    # -- writes (deployments only; discovery never calls these) -----------

    def makedirs(self, path: str, mode: int = 0o755) -> None:
        self.real(path).mkdir(parents=True, exist_ok=True, mode=mode)

    def write_text(self, path: str, content: str, mode: int = 0o644) -> None:
        """Atomic write: temporary file in the same directory, then rename."""
        real = self.real(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        tmp = real.with_name(f".{real.name}.mas-tmp")
        tmp.write_text(content, encoding="utf-8", newline="\n")
        os.chmod(tmp, mode)
        os.replace(tmp, real)

    def backup(self, path: str) -> str | None:
        """Copy ``path`` to ``<path>.mas-bak-<timestamp>`` before it is changed."""
        real = self.real(path)
        if not real.is_file():
            return None
        dest = real.with_name(f"{real.name}.mas-bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(real, dest)
        return self.virtual(dest)

    def symlink_atomic(self, target: str, link: str) -> None:
        real_link = self.real(link)
        tmp = real_link.with_name(f".{real_link.name}.mas-tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(target, tmp)
        os.replace(tmp, real_link)

    def remove(self, path: str) -> None:
        real = self.real(path)
        if real.is_symlink() or real.is_file():
            real.unlink()
        elif real.is_dir():
            shutil.rmtree(real)

    def rename(self, src: str, dst: str) -> None:
        os.replace(self.real(src), self.real(dst))

    # -- commands --------------------------------------------------------

    def stream(self, argv: list[str], on_line, cwd: str | None = None, env: dict | None = None,
               timeout: float = 1800) -> int | None:
        """Run a command, calling ``on_line`` for each output line. Returns the exit
        code, or None if the binary is missing. ``env`` replaces the environment."""
        # mas.service runs with UMask=0077; built files must stay readable by nginx/php-fpm.
        extra = {"umask": 0o022} if os.name == "posix" else {}
        try:
            proc = subprocess.Popen(
                argv, cwd=self.real(cwd) if cwd else None, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", **extra,
            )
        except FileNotFoundError:
            return None
        deadline = time.monotonic() + timeout
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
            if time.monotonic() > deadline:
                proc.kill()
                on_line(f"[mas] timeout after {int(timeout)}s, command killed")
                return 124
        return proc.wait()

    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult | None:
        """Run a command. Returns None when the binary is missing or times out."""
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return None
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("command %s failed: %s", argv[0], exc)
            return None
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    @property
    def euid(self) -> int:
        geteuid = getattr(os, "geteuid", None)
        return geteuid() if geteuid else 0

    # -- docker ----------------------------------------------------------

    def docker_request(self, method: str, api_path: str) -> bytes:
        """Call the Docker Engine API over the unix socket. Raises on failure."""
        conn = _UnixHTTPConnection(str(self.real(DOCKER_SOCKET)), self.timeout)
        try:
            conn.request(method, api_path)
            resp = conn.getresponse()
            body = resp.read()
            if resp.status not in (200, 204, 304):  # 304: already started/stopped
                raise OSError(f"docker API {method} {api_path} returned HTTP {resp.status}")
            return body
        finally:
            conn.close()

    def docker_get(self, api_path: str) -> Any:
        return json.loads(self.docker_request("GET", api_path))


class FixtureHost(Host):
    """A fake server described by a directory.

    Layout::

        <fixture>/root/...        virtual filesystem ("/" of the fake server)
        <fixture>/commands.json   {"<argv fnmatch pattern>": "<file with stdout>"}
        <fixture>/docker.json     {"<api path>": "<file with JSON body>"}

    Symlinks are awkward to commit (and to create on Windows), so a plain file
    is accepted where a symlink is expected: its content is the link target.
    """

    def __init__(self, fixture_dir: str | Path) -> None:
        self.fixture_dir = Path(fixture_dir)
        super().__init__(self.fixture_dir / "root")
        self._commands = self._load_map("commands.json")
        self._docker = self._load_map("docker.json")
        self.executed: list[list[str]] = []
        self.symlinks: set[str] = set()
        self.stream_hooks: list = []  # tests: callables(argv, cwd, env) run before stream()

    def _load_map(self, name: str) -> dict[str, Any]:
        path = self.fixture_dir / name
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def readlink(self, path: str) -> str | None:
        real = self.real(path)
        if real.is_symlink():
            return os.readlink(real)
        if real.is_file():
            return real.read_text(encoding="utf-8").strip()
        return None

    def is_symlink(self, path: str) -> bool:
        real = self.real(path)
        return real.is_symlink() or (real.is_file() and real.name in self.symlinks)

    def symlink_atomic(self, target: str, link: str) -> None:
        # Plain file holding the target (see readlink), works without privileges.
        self.write_text(link, target)
        self.symlinks.add(self.real(link).name)

    def owner_uid(self, path: str) -> int | None:
        return 0 if self.exists(path) else None

    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult | None:
        self.executed.append(list(argv))
        joined = " ".join(argv)
        for pattern, spec in self._commands.items():
            if not fnmatch.fnmatchcase(joined, pattern):
                continue
            if isinstance(spec, str):
                spec = {"stdout": spec}
            stdout = spec.get("output", "")
            if spec.get("stdout"):
                stdout = (self.fixture_dir / spec["stdout"]).read_text(encoding="utf-8")
            return CommandResult(spec.get("returncode", 0), stdout, spec.get("stderr", ""))
        return None

    def stream(self, argv: list[str], on_line, cwd: str | None = None, env: dict | None = None,
               timeout: float = 1800) -> int | None:
        for hook in self.stream_hooks:
            hook(argv, cwd, env)
        res = self.run(argv)
        if res is None:
            return None
        for line in (res.stdout + res.stderr).splitlines():
            on_line(line)
        return res.returncode

    @property
    def euid(self) -> int:
        return 0

    def docker_request(self, method: str, api_path: str) -> bytes:
        if not self.exists(DOCKER_SOCKET):
            raise FileNotFoundError(DOCKER_SOCKET)
        if method != "GET":
            self.executed.append(["docker-api", method, api_path])
            return b""
        if api_path not in self._docker:
            raise OSError(f"docker API {api_path} not in fixture")
        return (self.fixture_dir / self._docker[api_path]).read_bytes()
