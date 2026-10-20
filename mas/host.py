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
import socket
import subprocess
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

    # -- commands --------------------------------------------------------

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

    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult | None:
        self.executed.append(list(argv))
        joined = " ".join(argv)
        for pattern, spec in self._commands.items():
            if not fnmatch.fnmatchcase(joined, pattern):
                continue
            if isinstance(spec, str):
                spec = {"stdout": spec}
            stdout = ""
            if spec.get("stdout"):
                stdout = (self.fixture_dir / spec["stdout"]).read_text(encoding="utf-8")
            return CommandResult(spec.get("returncode", 0), stdout, spec.get("stderr", ""))
        return None

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
