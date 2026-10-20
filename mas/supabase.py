"""Self-hosted Supabase, installed from the official docker compose stack.

One instance per server (the stack needs 2-4 GB of RAM). Every published
port is bound to 127.0.0.1; only the public API (/rest, /auth, /realtime,
/storage, /functions, /graphql) goes through nginx. Studio and pg-meta stay
private: open them through an SSH tunnel to the Kong port.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from mas import templates
from mas.config import Config
from mas.db import Database
from mas.deploy import (
    UPGRADE_MAP_PATH, DeployError, Deployer, ReleaseLog, domain_conflict, used_ports, vhost_paths,
)
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import Host

log = logging.getLogger(__name__)

INSTANCE_ID = "supabase"
PROJECT = "mas-supabase"
DB_CONTAINER = "supabase-db"  # container_name in the official compose file
REPOSITORY = "https://github.com/supabase/supabase.git"
MIN_MEMORY_MB = 2048
JWT_TTL = 10 * 365 * 86400


class SupabaseError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt_hs256(payload: dict, secret: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


def generate_secrets(now: int | None = None) -> dict[str, str]:
    now = int(now or time.time())
    jwt_secret = secrets.token_hex(32)
    claims = {"iss": "supabase", "iat": now, "exp": now + JWT_TTL}
    return {
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "JWT_SECRET": jwt_secret,
        "ANON_KEY": jwt_hs256({**claims, "role": "anon"}, jwt_secret),
        "SERVICE_ROLE_KEY": jwt_hs256({**claims, "role": "service_role"}, jwt_secret),
        "DASHBOARD_USERNAME": "supabase",
        "DASHBOARD_PASSWORD": secrets.token_urlsafe(18),
        "SECRET_KEY_BASE": secrets.token_hex(32),
        "VAULT_ENC_KEY": secrets.token_hex(16),
        "PG_META_CRYPTO_KEY": secrets.token_hex(16),
        "LOGFLARE_PUBLIC_ACCESS_TOKEN": secrets.token_hex(24),
        "LOGFLARE_PRIVATE_ACCESS_TOKEN": secrets.token_hex(24),
        "LOGFLARE_API_KEY": secrets.token_hex(24),
    }


def render_env(example: str, values: dict[str, str]) -> str:
    """Fill ``.env.example``: replace the keys we know, keep everything else, append the rest."""
    out, seen = [], set()
    for line in example.splitlines():
        key, sep, _ = line.partition("=")
        key = key.strip()
        if sep and not line.lstrip().startswith("#") and key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in values if k not in seen]
    if missing:
        out += ["", "# Added by Mas"] + [f"{k}={values[k]}" for k in missing]
    return "\n".join(out) + "\n"


_PORT_RE = re.compile(r"""^(\s*-\s*["']?)(\$\{[A-Z0-9_]+\}:)""", re.M)


def bind_loopback(compose: str) -> str:
    """``- ${KONG_HTTP_PORT}:8000`` → ``- 127.0.0.1:${KONG_HTTP_PORT}:8000`` (Docker bypasses the firewall)."""
    return _PORT_RE.sub(r"\g<1>127.0.0.1:\g<2>", compose)


def memory_available_mb(host: Host) -> int | None:
    for line in (host.read_text("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return None


def requirements(host: Host) -> dict:
    docker = host.run(["docker", "version", "--format", "{{.Server.Version}}"])
    compose = host.run(["docker", "compose", "version", "--short"])
    return {
        "docker": docker.stdout.strip() if docker is not None and docker.ok else None,
        "compose": compose.stdout.strip() if compose is not None and compose.ok else None,
        "memory_available_mb": memory_available_mb(host),
        "memory_recommended_mb": MIN_MEMORY_MB,
    }


def api_health(url: str, anon_key: str, timeout: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = "no answer"
    while True:
        req = urllib.request.Request(url, headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(getattr(exc, "reason", exc))
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(5)


class SupabaseManager:
    def __init__(self, host: Host, config: Config, db: Database, deployer: Deployer,
                 health: Callable[[str, str, float], tuple[bool, str]] = api_health,
                 health_timeout: float = 300) -> None:
        self.host, self.config, self.db, self.deployer = host, config, db, deployer
        self.health = health
        self.health_timeout = health_timeout
        self.dir = f"{config.supabase_dir.rstrip('/')}/{INSTANCE_ID}"
        self.data_dir = Path(db.path).parent
        self._lock = threading.Lock()

    # -- state -----------------------------------------------------------

    def get(self) -> dict | None:
        return self.db.get_supabase(INSTANCE_ID)

    def log_path(self) -> Path:
        return self.data_dir / "logs" / "supabase" / "install.log"

    def backups_dir(self) -> Path:
        return self.data_dir / "backups" / "supabase"

    def secrets(self, inst: dict) -> dict[str, str]:
        return decrypt_env(load_fernet(self.host, self.config.secret_key_path), inst["secrets"])

    def ports(self, inst: dict) -> dict[str, int]:
        return json.loads(inst["ports"] or "{}")

    def api_url(self, inst: dict) -> str:
        return f"https://{inst['domain']}" if inst["domain"] else f"http://127.0.0.1:{self.ports(inst)['kong_http']}"

    def connection_env(self) -> dict[str, str]:
        inst = self.get()
        if not inst or inst["status"] != "running":
            raise SupabaseError("Supabase is not running")
        s, ports, url = self.secrets(inst), self.ports(inst), self.api_url(inst)
        return {
            "SUPABASE_URL": url,
            "NEXT_PUBLIC_SUPABASE_URL": url,
            "SUPABASE_ANON_KEY": s["ANON_KEY"],
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": s["ANON_KEY"],
            "SUPABASE_SERVICE_ROLE_KEY": s["SERVICE_ROLE_KEY"],
            "DATABASE_URL": f"postgresql://postgres:{s['POSTGRES_PASSWORD']}@127.0.0.1:{ports['postgres']}/postgres",
        }

    def credentials(self) -> dict:
        inst = self.get()
        if not inst or not inst["secrets"]:
            raise SupabaseError("no Supabase instance")
        s, ports = self.secrets(inst), self.ports(inst)
        kong = ports.get("kong_http")
        return {
            "api_url": self.api_url(inst),
            "anon_key": s["ANON_KEY"],
            "service_role_key": s["SERVICE_ROLE_KEY"],
            "database_url": f"postgresql://postgres:{s['POSTGRES_PASSWORD']}@127.0.0.1:{ports.get('postgres')}/postgres",
            "studio": {"tunnel": f"ssh -L 8000:127.0.0.1:{kong} <you>@<server>", "url": "http://localhost:8000",
                       "username": s["DASHBOARD_USERNAME"], "password": s["DASHBOARD_PASSWORD"]},
        }

    # -- compose -----------------------------------------------------------

    def _compose(self, *args: str) -> list[str]:
        return ["docker", "compose", "-p", PROJECT, "--project-directory", str(self.host.real(self.dir)),
                "--env-file", str(self.host.real(f"{self.dir}/.env")),
                "-f", str(self.host.real(f"{self.dir}/docker-compose.yml")), *args]

    def action(self, action: str) -> tuple[bool, str]:
        if action not in ("start", "stop", "restart"):
            raise SupabaseError(f"unknown action {action}")
        if not self.get():
            raise SupabaseError("no Supabase instance")
        res = self.host.run(self._compose(action), timeout=600)
        ok = res is not None and res.ok
        if ok:
            self.db.save_supabase(INSTANCE_ID, status="stopped" if action == "stop" else "running")
        return ok, (res.stdout + res.stderr).strip() if res else "docker not found"

    # -- install -------------------------------------------------------------

    def start_install(self, domain: str | None) -> None:
        current = self.get()
        if current and current["status"] in ("installing", "running", "stopped"):
            raise SupabaseError("Supabase is already installed")
        if self.db.get_app(INSTANCE_ID):
            raise SupabaseError("an app is already called 'supabase'")
        self.db.save_supabase(INSTANCE_ID, status="installing", domain=domain, dir=self.dir, error=None)
        threading.Thread(target=self.install, daemon=True, name="mas-supabase").start()

    def _allocate_ports(self, count: int) -> list[int]:
        used = used_ports(self.host, self.db)
        free = [p for p in range(self.config.port_min, self.config.port_max + 1) if p not in used]
        if len(free) < count:
            raise SupabaseError("not enough free ports")
        return free[:count]

    def install(self, echo: Callable[[str], None] | None = None) -> bool:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        rlog = ReleaseLog(path, echo)
        try:
            with self._lock:
                self._install(rlog)
            rlog.step("Supabase is running")
            return True
        except (SupabaseError, DeployError) as exc:
            rlog.line(f"error: {exc}")
            self.db.save_supabase(INSTANCE_ID, status="failed", error=str(exc)[:500])
            return False
        except Exception as exc:  # noqa: BLE001
            log.exception("supabase install failed")
            rlog.line(f"unexpected error: {exc!r}")
            self.db.save_supabase(INSTANCE_ID, status="failed", error=repr(exc)[:500])
            return False
        finally:
            rlog.close()

    def _install(self, rlog: ReleaseLog) -> None:
        inst = self.get() or {}
        domain = inst.get("domain")
        rlog.step("Checking requirements")
        reqs = requirements(self.host)
        if not reqs["docker"]:
            raise SupabaseError("Docker is not running (apt install docker.io)")
        if not reqs["compose"]:
            raise SupabaseError("docker compose v2 is missing (apt install docker-compose-plugin)")
        mem = reqs["memory_available_mb"]
        rlog.line(f"docker {reqs['docker']}, compose {reqs['compose']}, {mem} MB available")
        if mem is not None and mem < MIN_MEMORY_MB:
            rlog.line(f"warning: Supabase needs about {MIN_MEMORY_MB} MB of free memory, only {mem} MB available")
        existing = self.host.run(["docker", "ps", "-a", "--filter", f"name=^{DB_CONTAINER}$", "--format", "{{.Names}}"])
        if existing is not None and existing.stdout.strip() and not self.host.exists(self.dir):
            raise SupabaseError(f"a container named {DB_CONTAINER} already exists (another Supabase?)")
        if domain:
            conflict = domain_conflict(self.host, domain, set(vhost_paths(INSTANCE_ID)))
            if conflict:
                raise SupabaseError(f"{domain} is already served by {conflict}")

        kong_http, kong_https, postgres, pooler = self._allocate_ports(4)
        ports = {"kong_http": kong_http, "kong_https": kong_https, "postgres": postgres, "pooler": pooler}

        rlog.step("Fetching the official Supabase docker stack")
        parent = str(PurePosixPath(self.dir).parent)
        self.deployer._mkdir_public(parent)
        tmp = f"{parent}/.tmp-supabase"
        self.host.remove(tmp)
        self.host.remove(self.dir)
        self.deployer._cmd(rlog, ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPOSITORY,
                                  self.deployer._r(tmp)], timeout=600)
        self.deployer._cmd(rlog, ["git", "-C", self.deployer._r(tmp), "sparse-checkout", "set", "docker"])
        head = self.host.run(["git", "-C", self.deployer._r(tmp), "rev-parse", "HEAD"])
        ref = head.stdout.strip() if head is not None and head.ok else None
        if not self.host.exists(f"{tmp}/docker/docker-compose.yml"):
            raise SupabaseError("the Supabase repository layout changed (docker/docker-compose.yml missing)")
        self.host.rename(f"{tmp}/docker", self.dir)
        self.host.remove(tmp)
        rlog.line(f"supabase/docker @ {ref[:10] if ref else '?'}")

        rlog.step("Generating secrets and configuration")
        values = generate_secrets()
        public = f"https://{domain}" if domain else f"http://127.0.0.1:{kong_http}"
        values.update({
            "SITE_URL": public, "API_EXTERNAL_URL": public, "SUPABASE_PUBLIC_URL": public,
            "KONG_HTTP_PORT": str(kong_http), "KONG_HTTPS_PORT": str(kong_https),
            "POSTGRES_PORT": str(postgres), "POOLER_PROXY_PORT_TRANSACTION": str(pooler),
            "STUDIO_DEFAULT_PROJECT": "Mas", "STUDIO_DEFAULT_ORGANIZATION": "Mas",
        })
        example = self.host.read_text(f"{self.dir}/.env.example") or ""
        self.host.write_text(f"{self.dir}/.env", render_env(example, values), mode=0o600)
        compose_path = f"{self.dir}/docker-compose.yml"
        self.host.write_text(compose_path, bind_loopback(self.host.read_text(compose_path) or ""))
        fernet = load_fernet(self.host, self.config.secret_key_path)
        keep = ("POSTGRES_PASSWORD", "JWT_SECRET", "ANON_KEY", "SERVICE_ROLE_KEY", "DASHBOARD_USERNAME",
                "DASHBOARD_PASSWORD")
        self.db.save_supabase(INSTANCE_ID, ports=json.dumps(ports), ref=ref,
                              secrets=encrypt_env(fernet, {k: values[k] for k in keep}))

        rlog.step("Pulling images (a few minutes the first time)")
        self.deployer._cmd(rlog, self._compose("pull", "--quiet"), timeout=3600)
        rlog.step("Starting the stack")
        self.deployer._cmd(rlog, self._compose("up", "-d"), timeout=1200)

        rlog.step("Waiting for the API")
        ok, detail = self.health(f"http://127.0.0.1:{kong_http}/rest/v1/", values["ANON_KEY"], self.health_timeout)
        rlog.line(f"api: {'ok' if ok else 'FAILED'} ({detail})")
        if not ok:
            raise SupabaseError(f"the Supabase API did not answer ({detail}); see `docker compose -p {PROJECT} logs`")

        if domain:
            rlog.step(f"Publishing the API on {domain}")
            self._install_vhost(domain, kong_http, rlog)
            if self.deployer._ensure_certificate({"id": INSTANCE_ID, "domain": domain}, rlog):
                self._install_vhost(domain, kong_http, rlog)
        self.db.save_supabase(INSTANCE_ID, status="running", error=None)

    def _install_vhost(self, domain: str, port: int, rlog: ReleaseLog) -> None:
        if self.host.read_text(UPGRADE_MAP_PATH) != templates.UPGRADE_MAP:
            self.deployer._nginx_apply(rlog, UPGRADE_MAP_PATH, templates.UPGRADE_MAP, None)
        self.deployer._ensure_pages()
        available, enabled = vhost_paths(INSTANCE_ID)
        content = templates.vhost(INSTANCE_ID, domain, templates.supabase_body(port, self.config.pages_dir),
                                  self.deployer._has_cert(domain))
        self.deployer._nginx_apply(rlog, available, content, enabled)

    # -- backups ---------------------------------------------------------------

    def backup(self) -> Path:
        inst = self.get()
        if not inst or inst["status"] != "running":
            raise SupabaseError("Supabase is not running")
        target_dir = self.backups_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"supabase-{stamp}.dump"
        inside = "/tmp/mas-backup.dump"
        steps = [
            ["docker", "exec", DB_CONTAINER, "pg_dump", "-U", "postgres", "-d", "postgres", "-Fc", "-f", inside],
            ["docker", "cp", f"{DB_CONTAINER}:{inside}", str(target)],
            ["docker", "exec", DB_CONTAINER, "rm", "-f", inside],
        ]
        for argv in steps:
            res = self.host.run(argv, timeout=1800)
            if res is None or not res.ok:
                raise SupabaseError(f"`{' '.join(argv[:4])}` failed: {(res.stderr if res else 'docker missing').strip()}")
        self.db.save_supabase(INSTANCE_ID, last_backup=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for old in sorted(target_dir.glob("supabase-*.dump"))[:-self.config.supabase_keep_backups]:
            old.unlink(missing_ok=True)
        return target

    def list_backups(self) -> list[dict]:
        out = []
        for p in sorted(self.backups_dir().glob("supabase-*.dump"), reverse=True):
            out.append({"name": p.name, "size": p.stat().st_size,
                        "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
        return out

    def auto_backup(self) -> None:
        """Called periodically: one backup a day while Supabase runs."""
        inst = self.get()
        if not inst or inst["status"] != "running":
            return
        last = inst.get("last_backup")
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < 86400:
            return
        try:
            self.backup()
        except SupabaseError as exc:
            log.warning("automatic Supabase backup failed: %s", exc)

    # -- removal -----------------------------------------------------------------

    def remove(self, purge: bool = False) -> list[str]:
        inst = self.get()
        if not inst:
            raise SupabaseError("no Supabase instance")
        done = []
        if self.host.exists(f"{self.dir}/docker-compose.yml"):
            res = self.host.run(self._compose("down", *(["-v"] if purge else [])), timeout=600)
            done.append("stack stopped" + (" and volumes deleted" if purge else ""))
            if res is None or not res.ok:
                done.append(f"warning: docker compose down failed: {(res.stderr if res else '').strip()}")
        available, enabled = vhost_paths(INSTANCE_ID)
        if self.host.exists(available) or self.host.exists(enabled):
            self.host.backup(available)
            self.host.remove(enabled)
            self.host.remove(available)
            test = self.host.run(["nginx", "-t"])
            if test is not None and test.ok:
                self.host.run(["systemctl", "reload", "nginx"])
            done.append(f"removed {available}")
        if purge:
            self.host.remove(self.dir)
            done.append(f"removed {self.dir} (backups kept in {self.backups_dir()})")
        self.db.delete_supabase(INSTANCE_ID)
        return done
