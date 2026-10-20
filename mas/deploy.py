"""Deployment pipeline (phase 3).

Managed apps live in ``/srv/mas/apps/<app>/``::

    releases/<sha>/   one checkout per deployed commit (5 kept)
    current -> releases/<sha>   switched atomically
    .home/            HOME of the dedicated user mas-<app>

Steps: clone, build (as mas-<app>), switch ``current``, (re)start the
generated systemd unit, write the nginx vhost (backup + nginx -t), get a
certificate, health check. A failed health check puts the previous release
back.

Adopted apps are services found by the scan whose folder is a GitHub
checkout: Mas fetches and resets that folder in place (refusing local
changes), rebuilds, and restarts it through its existing manager.

Deployments run one at a time (thread lock + file lock shared with the CLI).
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from mas import templates
from mas.actions import ActionError, available_actions, run_action
from mas.collectors import ScanContext
from mas.collectors import nginx as nginx_collector
from mas.collectors.ports import parse_ss_tcp
from mas.collectors.processes import load_users
from mas.config import Config
from mas.db import Database
from mas.dns import server_ips
from mas.envcrypt import EnvError, decrypt_env, load_fernet, systemd_env_file
from mas.health import _resolve
from mas.github import GitHubApp, GitHubError, git_auth_env
from mas.host import Host
from mas.models import Service, ServiceType

log = logging.getLogger(__name__)

APP_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}$")
PROXY_TYPES = {ServiceType.NEXTJS, ServiceType.NODE, ServiceType.PYTHON}
BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
UPGRADE_MAP_PATH = "/etc/nginx/conf.d/mas-maps.conf"
ENV_DIR = "/etc/mas/apps"


class DeployError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- naming ----------------------------------------------------------------


@dataclass
class AppPaths:
    base: str
    releases: str
    current: str
    home: str


def app_paths(config: Config, app_id: str) -> AppPaths:
    base = f"{config.apps_dir.rstrip('/')}/{app_id}"
    return AppPaths(base, f"{base}/releases", f"{base}/current", f"{base}/.home")


def app_user(app_id: str) -> str:
    return f"mas-{app_id}"


def unit_name(app_id: str) -> str:
    return f"mas-app-{app_id}.service"


def unit_path(app_id: str) -> str:
    return f"/etc/systemd/system/{unit_name(app_id)}"


def vhost_paths(app_id: str) -> tuple[str, str]:
    return (f"/etc/nginx/sites-available/mas-app-{app_id}.conf", f"/etc/nginx/sites-enabled/mas-app-{app_id}.conf")


def env_file(app_id: str) -> str:
    return f"{ENV_DIR}/{app_id}.env"


# -- validation & creation ---------------------------------------------------


def validate_app(app_id: str, repo: str, branch: str, domain: str | None, app_type: str) -> None:
    if not APP_ID_RE.fullmatch(app_id):
        raise DeployError("app id: 1-24 chars, lowercase letters, digits and dashes")
    if not REPO_RE.fullmatch(repo):
        raise DeployError("repository must look like owner/name")
    if not BRANCH_RE.fullmatch(branch):
        raise DeployError("invalid branch name")
    if domain and not DOMAIN_RE.fullmatch(domain):
        raise DeployError(f"invalid domain: {domain}")
    if app_type not in {t.value for t in ServiceType} or app_type == "unknown":
        raise DeployError("type must be one of nextjs, node, python, static, php")


def used_ports(host: Host, db: Database) -> set[int]:
    used = {a["port"] for a in db.list_apps() if a["port"]}
    res = host.run(["ss", "-tlnH"])
    if res is not None and res.ok:
        used |= {s.port for s in parse_ss_tcp(res.stdout) if s.port}
    return used


def allocate_port(host: Host, config: Config, db: Database) -> int:
    used = used_ports(host, db)
    for port in range(config.port_min, config.port_max + 1):
        if port not in used:
            return port
    raise DeployError(f"no free port between {config.port_min} and {config.port_max}")


def domain_conflict(host: Host, domain: str, own_files: set[str]) -> str | None:
    """File of an existing vhost already serving ``domain`` (other than ours)."""
    sites = nginx_collector.collect(ScanContext(host))
    for site in sites:
        if domain in site.names and not set(site.files) <= own_files:
            return ", ".join(f for f in site.files if f not in own_files)
    return None


def suggest_start_command(app_type: ServiceType, files: set[str]) -> str | None:
    if app_type == ServiceType.PYTHON:
        if "manage.py" in files:
            return ".venv/bin/gunicorn --bind 127.0.0.1:$PORT config.wsgi"
        return ".venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT"
    return None


# -- health check --------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 - keep 3xx as the answer
        return None


def http_health(url: str, host_header: str | None, timeout: float) -> tuple[bool, str]:
    """Poll ``url`` until it answers with a status < 500 or ``timeout`` expires."""
    opener = urllib.request.build_opener(_NoRedirect)
    deadline = time.monotonic() + timeout
    last = "no answer"
    while True:
        req = urllib.request.Request(url, headers={"Host": host_header} if host_header else {})
        try:
            with opener.open(req, timeout=5) as resp:
                return True, f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return True, f"HTTP {exc.code}"
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(getattr(exc, "reason", exc))
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(2)


# -- logging ---------------------------------------------------------------------


class ReleaseLog:
    SECRET_RE = re.compile(r"(x-access-token:|AUTHORIZATION: basic )\S+", re.I)

    def __init__(self, path: Path, echo: Callable[[str], None] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._echo = echo

    def line(self, text: str) -> None:
        text = self.SECRET_RE.sub(r"\1***", text)
        self._fh.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        if self._echo:
            self._echo(text)

    def step(self, text: str) -> None:
        self.line(f"==> {text}")

    def close(self) -> None:
        self._fh.close()


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        try:
            import fcntl
        except ImportError:  # Windows dev machines
            yield
            return
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# -- pipeline ----------------------------------------------------------------------


class Deployer:
    def __init__(
        self,
        host: Host,
        config: Config,
        db: Database,
        github: Callable[[], GitHubApp | None],
        find_service: Callable[[str], Service | None] = lambda _id: None,
        health_check: Callable[[str, str | None, float], tuple[bool, str]] = http_health,
        on_change: Callable[[], None] | None = None,
        resolver: Callable[[str], list[str]] = _resolve,
    ) -> None:
        self.host, self.config, self.db = host, config, db
        self.resolver = resolver
        self.github = github
        self.find_service = find_service
        self.health_check = health_check
        self.on_change = on_change
        self.data_dir = Path(db.path).parent
        self.queue: queue.Queue[int] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # -- queue -------------------------------------------------------------

    def enqueue(self, app_id: str, trigger: str, actor: str | None, sha: str | None = None,
                rollback_to: int | None = None) -> int:
        if self.db.get_app(app_id) is None:
            raise DeployError(f"unknown app {app_id}")
        rid = self.db.create_release(app_id, trigger, actor, sha)
        if rollback_to is not None:
            target = self.db.get_release(rollback_to)
            if not target or target["app_id"] != app_id or not target["path"]:
                self.db.update_release(rid, status="failed", error="release not available", finished_at=_now())
                raise DeployError("this release is not available anymore")
            self.db.update_release(rid, sha=target["sha"], path=target["path"],
                                   message=f"Rollback to #{target['id']}: {target['message'] or ''}".strip())
        self.queue.put(rid)
        self._start_worker()
        return rid

    def _start_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="mas-deploy", daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        while True:
            rid = self.queue.get()
            try:
                self.run(rid)
            except Exception:
                log.exception("deployment %s crashed", rid)

    def log_path(self, app_id: str, release_id: int) -> Path:
        return self.data_dir / "logs" / app_id / f"{release_id}.log"

    def run(self, release_id: int, echo: Callable[[str], None] | None = None) -> bool:
        with self._lock, _file_lock(self.data_dir / "deploy.lock"):
            release = self.db.get_release(release_id)
            app = self.db.get_app(release["app_id"]) if release else None
            if release is None or app is None:
                return False
            path = self.log_path(app["id"], release_id)
            self.db.update_release(release_id, status="building", log_path=str(path))
            rlog = ReleaseLog(path, echo)
            try:
                if release["trigger"] == "rollback":
                    self._rollback(app, release, rlog)
                elif app["mode"] == "adopted":
                    self._adopted(app, release, rlog)
                else:
                    self._managed(app, release, rlog)
                rlog.step(f"Release #{release_id} is live")
                return True
            except (DeployError, GitHubError, EnvError, ActionError) as exc:
                rlog.line(f"error: {exc}")
                self.db.update_release(release_id, status="failed", error=str(exc)[:500], finished_at=_now())
                return False
            except Exception as exc:
                log.exception("deployment %s failed", release_id)
                rlog.line(f"unexpected error: {exc!r}")
                self.db.update_release(release_id, status="failed", error=repr(exc)[:500], finished_at=_now())
                return False
            finally:
                rlog.close()
                if self.on_change:
                    self.on_change()

    # -- helpers -------------------------------------------------------------

    def _r(self, path: str) -> str:
        return str(self.host.real(path))

    def _tool_path(self) -> str:
        dirs = []
        for tool in ("node", "npm", "python3", "composer"):
            found = shutil.which(tool)
            if found and str(PurePosixPath(found).parent) not in dirs:
                dirs.append(str(PurePosixPath(found).parent))
        return ":".join(dirs + [BASE_PATH])

    def _cmd(self, rlog: ReleaseLog, argv: list[str], cwd: str | None = None, env: dict | None = None,
             user: str | None = None, timeout: float = 1800, check: bool = True) -> int:
        full_env = {"PATH": self._tool_path(), "LANG": "C.UTF-8", "CI": "1", "HOME": "/root", **(env or {})}
        if user and user != "root":
            argv = ["runuser", "-u", user, "--", *argv]
        rlog.line("$ " + " ".join(argv))
        rc = self.host.stream(argv, rlog.line, cwd=cwd, env=full_env, timeout=timeout)
        if rc is None:
            raise DeployError(f"{argv[0]} not found")
        if check and rc != 0:
            raise DeployError(f"`{' '.join(argv[:6])}` exited with {rc}")
        return rc

    def _system(self, rlog: ReleaseLog, argv: list[str]) -> None:
        res = self.host.run(argv, timeout=120)
        if res is None or not res.ok:
            detail = (res.stderr or res.stdout).strip() if res else "not found"
            raise DeployError(f"`{' '.join(argv)}` failed: {detail}")

    def _ensure_user(self, user: str, home: str, rlog: ReleaseLog) -> None:
        res = self.host.run(["id", "-u", user])
        if res is not None and res.ok:
            return
        rlog.line(f"creating system user {user}")
        cmd = ["useradd", "--system", "--user-group", "--home-dir", self._r(home), "--no-create-home",
               "--shell", "/usr/sbin/nologin", user]
        # mas.service has a read-only /etc: let systemd (PID 1) run useradd.
        res = self.host.run(["systemd-run", "--quiet", "--wait", "--collect", "--pipe", *cmd], timeout=60)
        if res is None or not res.ok:
            res = self.host.run(cmd, timeout=60)
        if res is None or not res.ok:
            raise DeployError(f"cannot create user {user}: {(res.stderr if res else 'useradd missing').strip()}")

    def _mkdir_public(self, path: str) -> None:
        self.host.makedirs(path)
        self.host.run(["chmod", "755", self._r(path)])

    def _package_scripts(self, rel: str) -> dict:
        try:
            return json.loads(self.host.read_text(f"{rel}/package.json") or "{}").get("scripts", {}) or {}
        except ValueError:
            return {}

    def _build(self, app: dict, app_type: ServiceType, workdir: str, user: str, env: dict, rlog: ReleaseLog) -> None:
        if app.get("build_command"):
            self._cmd(rlog, ["sh", "-c", app["build_command"]], cwd=workdir, env=env, user=user)
            return
        files = set(self.host.listdir(workdir))
        if app_type in (ServiceType.NEXTJS, ServiceType.NODE) or (
            app_type == ServiceType.STATIC and "package.json" in files
        ):
            install = ["npm", "ci"] if "package-lock.json" in files else ["npm", "install"]
            self._cmd(rlog, [*install, "--no-audit", "--no-fund"], cwd=workdir, env=env, user=user)
            if "build" in self._package_scripts(workdir):
                self._cmd(rlog, ["npm", "run", "build"], cwd=workdir, env=env, user=user)
        elif app_type == ServiceType.PYTHON:
            self._cmd(rlog, ["python3", "-m", "venv", ".venv"], cwd=workdir, env=env, user=user)
            if "requirements.txt" in files:
                self._cmd(rlog, [".venv/bin/pip", "install", "-r", "requirements.txt"], cwd=workdir, env=env, user=user)
            elif "pyproject.toml" in files:
                self._cmd(rlog, [".venv/bin/pip", "install", "."], cwd=workdir, env=env, user=user)
        elif app_type == ServiceType.PHP and "composer.json" in files and shutil.which("composer"):
            self._cmd(rlog, ["composer", "install", "--no-dev", "--no-interaction", "--optimize-autoloader"],
                      cwd=workdir, env=env, user=user)
        else:
            rlog.line("nothing to build")

    def _exec_start(self, app: dict, app_type: ServiceType, current: str) -> str:
        if app.get("start_command"):
            return templates.systemd_quote(app["start_command"])
        if app_type == ServiceType.NEXTJS:
            return f"{current}/node_modules/.bin/next start -H 127.0.0.1 -p {app['port']}"
        if app_type == ServiceType.NODE:
            return f"{shutil.which('npm') or '/usr/bin/npm'} start"
        raise DeployError("a start command is required for python apps (e.g. .venv/bin/uvicorn main:app "
                          "--host 127.0.0.1 --port $PORT)")

    def _install_unit(self, app: dict, app_type: ServiceType, paths: AppPaths, env: dict, rlog: ReleaseLog) -> None:
        self.host.makedirs(ENV_DIR, mode=0o700)
        self.host.write_text(env_file(app["id"]), systemd_env_file(env), mode=0o600)
        content = templates.unit(
            app["id"], app["repo"], app_user(app["id"]), paths.current, env_file(app["id"]),
            app["port"], self._tool_path(), self._exec_start(app, app_type, paths.current),
        )
        path = unit_path(app["id"])
        if self.host.read_text(path) != content:
            rlog.line(f"writing {path}")
            self.host.backup(path)
            self.host.write_text(path, content)
            self._system(rlog, ["systemctl", "daemon-reload"])
            self._system(rlog, ["systemctl", "enable", unit_name(app["id"])])
        rlog.step(f"Restarting {unit_name(app['id'])}")
        self._system(rlog, ["systemctl", "restart", unit_name(app["id"])])

    def _fpm_socket(self) -> str:
        sockets = sorted(self.host.glob("/run/php/php*-fpm.sock"))
        if not sockets:
            raise DeployError("no PHP-FPM socket found in /run/php (apt install php-fpm)")
        return sockets[-1]

    def _vhost_body(self, app: dict, app_type: ServiceType, paths: AppPaths) -> str:
        root = paths.current + (f"/{app['root_dir'].strip('/')}" if app.get("root_dir") else "")
        if app_type in PROXY_TYPES:
            return templates.proxy_body(app["port"])
        if app_type == ServiceType.PHP:
            return templates.php_body(root, self._fpm_socket())
        return templates.static_body(root)

    def _nginx_apply(self, rlog: ReleaseLog, path: str, content: str, link: str | None) -> None:
        """Write a config file, test, reload; restore the previous state if the test fails."""
        old = self.host.read_text(path)
        if old == content and (link is None or self.host.exists(link)):
            return
        backup = self.host.backup(path)
        self.host.write_text(path, content)
        created_link = False
        if link and not self.host.exists(link):
            self.host.symlink_atomic(path, link)
            created_link = True
        test = self.host.run(["nginx", "-t"], timeout=60)
        if test is None or not test.ok:
            if backup:
                self.host.write_text(path, self.host.read_text(backup) or "")
            else:
                self.host.remove(path)
            if created_link:
                self.host.remove(link)
            detail = (test.stderr if test else "nginx not found").strip()
            raise DeployError(f"nginx -t failed, previous configuration restored:\n{detail}")
        rlog.line(f"wrote {path} (nginx -t ok)")
        self._system(rlog, ["systemctl", "reload", "nginx"])

    def _install_vhost(self, app: dict, app_type: ServiceType, paths: AppPaths, rlog: ReleaseLog) -> None:
        available, enabled = vhost_paths(app["id"])
        conflict = domain_conflict(self.host, app["domain"], {available, enabled})
        if conflict:
            raise DeployError(f"{app['domain']} is already served by {conflict}")
        if app_type in PROXY_TYPES and self.host.read_text(UPGRADE_MAP_PATH) != templates.UPGRADE_MAP:
            self._nginx_apply(rlog, UPGRADE_MAP_PATH, templates.UPGRADE_MAP, None)
        https = self.host.exists(f"/etc/letsencrypt/live/{app['domain']}/fullchain.pem")
        content = templates.vhost(app["id"], app["domain"], self._vhost_body(app, app_type, paths), https)
        self._nginx_apply(rlog, available, content, enabled)

    def _wait_dns(self, domain: str, rlog: ReleaseLog) -> None:
        """Wait (bounded) until a freshly created DNS record is visible."""
        expected = server_ips(self.db, self.config)[0]
        if not expected or self.config.dns_wait_seconds <= 0:
            return
        deadline = time.monotonic() + self.config.dns_wait_seconds
        while True:
            ips = self.resolver(domain)
            if expected in ips:
                rlog.line(f"DNS: {domain} → {expected}")
                return
            if time.monotonic() >= deadline:
                rlog.line(f"DNS: {domain} resolves to {', '.join(ips) or 'nothing'} (expected {expected}), "
                          "trying anyway")
                return
            time.sleep(5)

    def _ensure_certificate(self, app: dict, app_type: ServiceType, paths: AppPaths, rlog: ReleaseLog) -> None:
        domain = app["domain"]
        if self.host.exists(f"/etc/letsencrypt/live/{domain}/fullchain.pem"):
            return
        self._wait_dns(domain, rlog)
        rlog.step(f"Requesting a certificate for {domain}")
        contact = ["-m", self.config.letsencrypt_email] if self.config.letsencrypt_email else \
            ["--register-unsafely-without-email"]
        rc = self._cmd(rlog, ["certbot", "certonly", "--nginx", "--non-interactive", "--agree-tos", *contact,
                              "--cert-name", domain, "-d", domain], timeout=300, check=False)
        if rc != 0:
            rlog.line(f"warning: no certificate for {domain} (DNS must point to this server); "
                      "the site is served over HTTP, the next deploy will retry")
            return
        self._install_vhost(app, app_type, paths, rlog)  # now with HTTPS

    def _health(self, app: dict, app_type: ServiceType, workdir: str, rlog: ReleaseLog,
                port: int | None = None) -> None:
        rlog.step("Health check")
        timeout = self.config.healthcheck_timeout
        port = port or (app.get("port") if app_type in PROXY_TYPES else None)
        if port:
            ok, detail = self.health_check(f"http://127.0.0.1:{port}/", None, timeout)
        elif app.get("domain"):
            ok, detail = self.health_check("http://127.0.0.1/", app["domain"], timeout)
        else:
            files = set(self.host.listdir(workdir))
            ok, detail = bool(files & {"index.html", "index.php"}), "index file"
        rlog.line(f"health: {'ok' if ok else 'FAILED'} ({detail})")
        if not ok:
            raise DeployError(f"health check failed: {detail}")

    def _mark_live(self, app: dict, release_id: int, path: str | None) -> None:
        for r in self.db.list_releases(app["id"], 200):
            if r["status"] == "live" and r["id"] != release_id:
                self.db.update_release(r["id"], status="superseded")
        self.db.update_release(release_id, status="live", path=path, finished_at=_now())
        self.db.update_app(app["id"], current_release=release_id)

    def _prune(self, app: dict, paths: AppPaths, rlog: ReleaseLog) -> None:
        """Keep the newest ``keep_releases`` release folders (and always ``current``)."""
        releases = [r for r in self.db.list_releases(app["id"], 500) if r["path"]]
        keep = {self.host.readlink(paths.current)}
        for r in releases:  # newest first
            if len(keep - {None}) >= self.config.keep_releases:
                break
            keep.add(r["path"])
        for r in releases:
            if r["path"] in keep:
                continue
            if self.host.exists(r["path"]):
                self.host.remove(r["path"])
                rlog.line(f"removed old release {PurePosixPath(r['path']).name}")
            self.db.update_release(r["id"], path=None)
        for name in self.host.listdir(paths.releases):
            if name.startswith(".tmp-"):
                self.host.remove(f"{paths.releases}/{name}")

    # -- managed apps ------------------------------------------------------------

    def _managed(self, app: dict, release: dict, rlog: ReleaseLog) -> None:
        gh = self.github()
        if gh is None:
            raise DeployError("GitHub App not configured (Settings → GitHub)")
        app_type = ServiceType(app["type"])
        paths = app_paths(self.config, app["id"])
        user = app_user(app["id"])
        installation = app["installation_id"] or gh.installation_for(app["repo"])
        rlog.step(f"Deploying {app['repo']}@{app['branch']} ({app_type.value}) → {app['domain'] or 'no domain'}")

        commit = gh.commit(installation, app["repo"], release["sha"] or app["branch"])
        rlog.line(f"commit {commit['sha'][:7]} {commit['message']} ({commit['author']})")
        self.db.update_release(release["id"], sha=commit["sha"], message=commit["message"], author=commit["author"])
        env_vars = decrypt_env(load_fernet(self.host, self.config.secret_key_path), app["env"])
        if app_type in PROXY_TYPES:
            self._exec_start(app, app_type, paths.current)  # fail early if python has no start command

        self._ensure_user(user, paths.home, rlog)
        for d in (self.config.apps_dir, paths.base, paths.releases):
            self._mkdir_public(d)
        self.host.makedirs(paths.home)
        self._system(rlog, ["chown", f"{user}:{user}", self._r(paths.home)])

        rlog.step("Cloning")
        tmp = f"{paths.releases}/.tmp-{release['id']}"
        self.host.remove(tmp)
        self._cmd(rlog, ["git", "clone", "--depth", "1", "--single-branch", "--branch", app["branch"],
                         f"https://github.com/{app['repo']}.git", self._r(tmp)],
                  env=git_auth_env(gh.token(installation)), timeout=600)
        head = self.host.run(["git", "-C", self._r(tmp), "rev-parse", "HEAD"])
        sha = head.stdout.strip() if head is not None and head.ok and head.stdout.strip() else commit["sha"]
        if sha != commit["sha"]:
            rlog.line(f"branch moved since the trigger, deploying {sha[:7]}")
            self.db.update_release(release["id"], sha=sha)
        previous = self.host.readlink(paths.current)
        rel = f"{paths.releases}/{sha}"
        if rel == previous:
            rel = f"{rel}-{release['id']}"
        self.host.remove(rel)
        self.host.rename(tmp, rel)
        self.db.update_release(release["id"], path=rel)
        self._system(rlog, ["chmod", "755", self._r(rel)])
        self._system(rlog, ["chown", "-R", f"{user}:{user}", self._r(rel)])

        rlog.step("Building")
        self._build(app, app_type, rel, user, {**env_vars, "HOME": self._r(paths.home)}, rlog)

        rlog.step(f"Switching current → {PurePosixPath(rel).name}")
        self.host.symlink_atomic(rel, paths.current)
        try:
            if app_type in PROXY_TYPES:
                self._install_unit(app, app_type, paths, env_vars, rlog)
            if app["domain"]:
                rlog.step("nginx")
                self._install_vhost(app, app_type, paths, rlog)
            self._health(app, app_type, rel, rlog)
        except (DeployError, EnvError) as exc:
            rlog.line(f"error: {exc}")
            if previous:
                rlog.step(f"Rolling back to {PurePosixPath(previous).name}")
                self.host.symlink_atomic(previous, paths.current)
                if app_type in PROXY_TYPES:
                    self.host.run(["systemctl", "restart", unit_name(app["id"])], timeout=120)
            raise

        if app["domain"]:
            self._ensure_certificate(app, app_type, paths, rlog)
        self._mark_live(app, release["id"], rel)
        self._prune(app, paths, rlog)

    def _rollback(self, app: dict, release: dict, rlog: ReleaseLog) -> None:
        target = release["path"]
        rlog.step(release["message"] or f"Rollback of {app['id']}")
        if app["mode"] == "adopted":
            self._adopted(app, release, rlog, reset_to=release["sha"])
            return
        if not target or not self.host.exists(target):
            raise DeployError("the release folder does not exist anymore")
        app_type = ServiceType(app["type"])
        paths = app_paths(self.config, app["id"])
        previous = self.host.readlink(paths.current)
        self.host.symlink_atomic(target, paths.current)
        try:
            if app_type in PROXY_TYPES:
                self._system(rlog, ["systemctl", "restart", unit_name(app["id"])])
            self._health(app, app_type, target, rlog)
        except DeployError:
            if previous:
                self.host.symlink_atomic(previous, paths.current)
                if app_type in PROXY_TYPES:
                    self.host.run(["systemctl", "restart", unit_name(app["id"])], timeout=120)
            raise
        self._mark_live(app, release["id"], target)

    # -- adopted apps ----------------------------------------------------------------

    def _adopted(self, app: dict, release: dict, rlog: ReleaseLog, reset_to: str | None = None) -> None:
        path = app["path"]
        if not path or not self.host.exists(f"{path}/.git"):
            raise DeployError(f"{path} is not a git checkout")
        app_type = ServiceType(app["type"])
        uid = self.host.owner_uid(path)
        owner = next((u.name for u in load_users(self.host).values() if u.uid == uid), "root")
        rlog.step(f"Updating {path} in place (as {owner})")

        def git(*args: str, env: dict | None = None) -> None:
            self._cmd(rlog, ["git", "-C", self._r(path), *args], env=env, user=owner, timeout=600)

        status = self.host.run(["runuser", "-u", owner, "--", "git", "-C", self._r(path), "status", "--porcelain",
                                "--untracked-files=no"] if owner != "root" else
                               ["git", "-C", self._r(path), "status", "--porcelain", "--untracked-files=no"])
        if status is None or not status.ok:
            raise DeployError("cannot read the git status of the folder")
        if status.stdout.strip():
            raise DeployError("the folder has uncommitted changes; commit or discard them first:\n" + status.stdout)
        head = self.host.run(["git", "-C", self._r(path), "rev-parse", "HEAD"])
        previous = head.stdout.strip() if head is not None and head.ok else None

        if reset_to:
            git("reset", "--hard", reset_to)
        else:
            gh = self.github()
            if gh is None:
                raise DeployError("GitHub App not configured (Settings → GitHub)")
            installation = app["installation_id"] or gh.installation_for(app["repo"])
            commit = gh.commit(installation, app["repo"], release["sha"] or app["branch"])
            self.db.update_release(release["id"], sha=commit["sha"], message=commit["message"],
                                   author=commit["author"])
            rlog.line(f"commit {commit['sha'][:7]} {commit['message']}")
            git("fetch", "--depth", "50", f"https://github.com/{app['repo']}.git", app["branch"],
                env=git_auth_env(gh.token(installation)))
            git("reset", "--hard", "FETCH_HEAD")
        head = self.host.run(["git", "-C", self._r(path), "rev-parse", "HEAD"])
        sha = head.stdout.strip() if head is not None and head.ok else release["sha"]
        self.db.update_release(release["id"], sha=sha, path=path)

        try:
            rlog.step("Building")
            self._build(app, app_type, path, owner, {}, rlog)
            svc = self.find_service(app["service_id"]) if app.get("service_id") else None
            if svc is not None:
                actions = available_actions(svc)
                action = "restart" if "restart" in actions else ("reload" if "reload" in actions else None)
                if action:
                    rlog.step(f"{action} {svc.id} ({svc.manager.value})")
                    result = run_action(self.host, svc, action)
                    for line in result.output.splitlines():
                        rlog.line(line)
                    if not result.ok:
                        raise DeployError(f"{action} failed")
                    self.db.audit("deploy", action, True, svc.id, result.command, result.output)
            self._health(app, app_type, path, rlog, port=svc.port if svc else None)
        except DeployError:
            if previous and previous != sha:
                rlog.step(f"Rolling back to {previous[:7]}")
                self._cmd(rlog, ["git", "-C", self._r(path), "reset", "--hard", previous], user=owner, check=False)
                svc = self.find_service(app["service_id"]) if app.get("service_id") else None
                if svc is not None and "restart" in available_actions(svc):
                    run_action(self.host, svc, "restart")
            raise
        self._mark_live(app, release["id"], path)

    # -- removal -----------------------------------------------------------------------

    def remove_app(self, app_id: str, purge_files: bool = False) -> list[str]:
        """Stop and unregister an app. Configs are backed up; files kept unless purge."""
        app = self.db.get_app(app_id)
        if app is None:
            raise DeployError(f"unknown app {app_id}")
        done = []
        if app["mode"] == "managed":
            unit = unit_path(app_id)
            if self.host.exists(unit):
                self.host.run(["systemctl", "disable", "--now", unit_name(app_id)], timeout=120)
                self.host.backup(unit)
                self.host.remove(unit)
                self.host.run(["systemctl", "daemon-reload"])
                done.append(f"removed {unit}")
            available, enabled = vhost_paths(app_id)
            if self.host.exists(available) or self.host.exists(enabled):
                self.host.backup(available)
                self.host.remove(enabled)
                self.host.remove(available)
                test = self.host.run(["nginx", "-t"])
                if test is not None and test.ok:
                    self.host.run(["systemctl", "reload", "nginx"])
                done.append(f"removed {available}")
            self.host.remove(env_file(app_id))
            if purge_files:
                self.host.remove(app_paths(self.config, app_id).base)
                done.append("removed release files")
        self.db.delete_app(app_id)
        return done
