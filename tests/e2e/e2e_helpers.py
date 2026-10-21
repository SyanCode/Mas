"""Helpers of the end-to-end tests (see conftest.py)."""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer
from mas.host import Host


def sh(*argv: str, check: bool = True, timeout: float = 600) -> str:
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise AssertionError(f"{' '.join(argv)} failed ({res.returncode}): {res.stderr.strip()[-2000:]}")
    return res.stdout


def http_get(url: str, timeout: float = 120) -> tuple[int, str]:
    """GET with retries until something answers (a container takes a moment to listen)."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read(4096).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
            time.sleep(2)
    raise AssertionError(f"{url} never answered: {last}")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex((host, port)) == 0


class Env:
    """A real Host, a database and a deployer in a temporary folder; deployments run inline."""

    def __init__(self, root: Path) -> None:
        self.host = Host()
        self.config = Config(cookie_secure=False, db_path=str(root / "mas.db"), apps_dir=str(root / "apps"),
                             pages_dir=str(root / "pages"), supabase_dir=str(root / "supabase"),
                             secret_key_path=str(root / "secret.key"), dns_wait_seconds=0, healthcheck_timeout=60)
        self.db = Database(root / "mas.db")
        self.deployer = Deployer(self.host, self.config, self.db, lambda: None)
        self.deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: \
            self.db.create_release(app_id, trigger, actor, sha)

    def deploy(self, release_id: int) -> None:
        ok = self.deployer.run(release_id)
        release = self.db.get_release(release_id)
        log = self.deployer.log_path(release["app_id"], release_id)
        assert ok, f"deployment failed: {release['error']}\n{log.read_text() if log.is_file() else ''}"

    def no_web_server(self) -> None:
        """Skip nginx and certbot: the deployment of websites is covered by the unit tests."""
        d = self.deployer
        d._install_vhost = lambda *a, **k: None
        d._has_cert = lambda domain: True
        d._ensure_certificate = lambda *a, **k: False
