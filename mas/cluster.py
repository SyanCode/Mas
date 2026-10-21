"""Several Mas servers working together: one drives, the others run copies of its apps.

Joining: server B creates a join token (shown once, stored hashed); server A adds B with its dashboard
URL and that token. A token lets its holder deploy code on the server: it is as sensitive as a root
password, and travels only over HTTPS.

Load balancing: an app of A can run on servers of the cluster too. When A deploys it, every chosen
server receives the same commit, a short-lived clone token from A's GitHub App and the app's settings;
it builds its copy, which listens on its private address (the network between the servers: a VPC, a
WireGuard or Tailscale network — never a public address). A adds the copies that answer to the nginx
upstream of the app; a server that fails is left out until the next deploy.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mas import __version__
from mas.config import Config
from mas.db import Database
from mas.deploy import PROXY_TYPES, DeployError, Deployer, ReleaseLog, allocate_port, app_instances
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import Host
from mas.models import ServiceType

TOKEN_PREFIX = "masc_"
ADDRESS_SETTING = "cluster_address"
NAME_SETTING = "cluster_name"
# Networks the copies may listen on: private ranges, CGNAT (Tailscale), IPv6 ULA.
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                   "100.64.0.0/10", "fc00::/7")]
BUILD_TIMEOUT = 1800


class ClusterError(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_address(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        raise ClusterError(f"{address!r} is not an IP address") from None
    if not any(ip in net for net in PRIVATE_NETS):
        raise ClusterError(f"{ip} is not a private address: the copies of the apps would be reachable from the "
                           "internet. Use the address of the private network between the servers (VPC, WireGuard, "
                           "Tailscale…)")
    return str(ip)


def validate_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("https://") or " " in url or len(url) > 300:
        raise ClusterError("the address of the other Mas must start with https:// (its dashboard)")
    return url


Opener = Callable[[str, str, dict, bytes | None, float], tuple[int, Any]]


def _urllib_opener(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return exc.code, {"detail": detail or exc.reason}


class Cluster:
    def __init__(self, host: Host, config: Config, db: Database, deployer: Deployer,
                 opener: Opener = _urllib_opener) -> None:
        self.host, self.config, self.db, self.deployer = host, config, db, deployer
        self.opener = opener
        deployer.spread = self.spread

    # -- this server -------------------------------------------------------------

    def name(self) -> str:
        return self.db.get_setting(NAME_SETTING) or socket.gethostname()

    def address(self) -> str | None:
        return self.db.get_setting(ADDRESS_SETTING)

    def configure(self, name: str | None, address: str | None) -> None:
        if name is not None:
            self.db.set_setting(NAME_SETTING, name.strip()[:60] or None)
        if address is not None:
            self.db.set_setting(ADDRESS_SETTING, validate_address(address) if address.strip() else None)

    def info(self) -> dict:
        """What the other servers see of this one (also shown on the Servers page)."""
        mem = {}
        for line in (self.host.read_text("/proc/meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            if rest.split() and rest.split()[0].isdigit():
                mem[key] = int(rest.split()[0]) // 1024
        load = (self.host.read_text("/proc/loadavg") or "0 0 0").split()[:3]
        uptime = (self.host.read_text("/proc/uptime") or "0").split()[0]
        try:
            disk = shutil.disk_usage(self.host.real("/"))
            disk_info = {"total_gb": round(disk.total / 1e9, 1), "free_gb": round(disk.free / 1e9, 1)}
        except OSError:
            disk_info = None
        apps = []
        for a in self.db.list_apps():
            live = self.db.get_release(a["current_release"]) if a["current_release"] else None
            apps.append({"id": a["id"], "name": a["name"], "type": a["type"], "domain": a["domain"],
                         "replica_of": a.get("replica_of"), "sha": (live or {}).get("sha"),
                         "status": (self.db.list_releases(a["id"], 1) or [{}])[0].get("status")})
        return {"name": self.name(), "version": __version__, "address": self.address(),
                "cpus": os.cpu_count(), "load": [float(x) for x in load],
                "memory": {"total_mb": mem.get("MemTotal"), "available_mb": mem.get("MemAvailable")},
                "disk": disk_info, "uptime_s": int(float(uptime)), "apps": apps}

    # -- join tokens (this server is driven) -------------------------------------------

    def create_token(self, name: str) -> str:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        self.db.create_cluster_token(name.strip()[:60] or "cluster", hash_token(token))
        return token

    def check_token(self, token: str | None) -> dict | None:
        if not token or not token.startswith(TOKEN_PREFIX):
            return None
        return self.db.use_cluster_token(hash_token(token))

    # -- the other servers (this server drives) ----------------------------------------

    def _fernet(self):
        return load_fernet(self.host, self.config.secret_key_path)

    def _call(self, server: dict, method: str, path: str, body: dict | None = None, timeout: float = 15) -> Any:
        token = decrypt_env(self._fernet(), server["token"]).get("token", "")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        try:
            status, payload = self.opener(method, f"{server['url']}{path}", headers, data, timeout)
        except (OSError, ValueError) as exc:
            raise ClusterError(f"{server['name']}: unreachable ({getattr(exc, 'reason', exc)})") from None
        if status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise ClusterError(f"{server['name']}: {detail or f'HTTP {status}'}")
        return payload

    def add_server(self, url: str, token: str) -> dict:
        url = validate_url(url)
        if any(s["url"] == url for s in self.db.list_cluster_servers()):
            raise ClusterError("this server is already in the cluster")
        sealed = encrypt_env(self._fernet(), {"token": token.strip()})
        probe = {"name": url, "url": url, "token": sealed}
        info = self._call(probe, "GET", "/api/cluster/self")
        if not info.get("address"):
            raise ClusterError(f"{info.get('name') or url} has no private address yet: set it on its Servers page")
        if info["address"] == self.address():
            raise ClusterError("that is this server")
        sid = self.db.add_cluster_server(info.get("name") or url, url, sealed, info["address"])
        return {"id": sid, "name": info.get("name"), "address": info["address"]}

    def remove_server(self, server_id: int) -> list[str]:
        """Leave a server: its copies are removed there, and from the apps here."""
        server = self.db.get_cluster_server(server_id)
        if server is None:
            raise ClusterError("unknown server")
        notes = []
        for app in self.db.list_apps():
            chosen = json.loads(app.get("cluster") or "[]")
            if server_id in chosen:
                try:
                    self._call(server, "DELETE", f"/api/cluster/copies/{app['id']}")
                except ClusterError as exc:
                    notes.append(f"{app['id']}: {exc}")
                self.db.update_app(app["id"], cluster=json.dumps([s for s in chosen if s != server_id]) or None)
                notes.append(f"{app['id']}: no copy on {server['name']} anymore (redeploy to update nginx)")
        self.db.delete_cluster_server(server_id)
        return notes

    def overview(self) -> dict:
        servers = self.db.list_cluster_servers()

        def probe(server: dict) -> dict:
            out = {"id": server["id"], "name": server["name"], "url": server["url"], "address": server["address"],
                   "info": None, "error": None, "latency_ms": None}
            started = time.monotonic()
            try:
                out["info"] = self._call(server, "GET", "/api/cluster/self", timeout=6)
                out["latency_ms"] = round((time.monotonic() - started) * 1000)
                if out["info"].get("address") and out["info"]["address"] != server["address"]:
                    self.db.update_cluster_server(server["id"], address=out["info"]["address"])
                    out["address"] = out["info"]["address"]
            except ClusterError as exc:
                out["error"] = str(exc)
            return out

        with ThreadPoolExecutor(max_workers=8) as pool:
            peers = list(pool.map(probe, servers))
        return {"self": self.info(), "servers": peers, "tokens": self.db.list_cluster_tokens()}

    # -- copies of an app (this server drives) ------------------------------------------

    def set_app_servers(self, app: dict, server_ids: list[int]) -> list[str]:
        if app["mode"] != "managed" or ServiceType(app["type"]) not in PROXY_TYPES or app.get("replica_of"):
            raise ClusterError("only Node.js, Next.js and Python apps deployed here can run on other servers")
        known = {s["id"]: s for s in self.db.list_cluster_servers()}
        unknown = [i for i in server_ids if i not in known]
        if unknown:
            raise ClusterError(f"unknown servers: {unknown}")
        before = json.loads(app.get("cluster") or "[]")
        notes = []
        for sid in set(before) - set(server_ids):  # copies to remove
            try:
                self._call(known[sid], "DELETE", f"/api/cluster/copies/{app['id']}")
                notes.append(f"copy removed from {known[sid]['name']}")
            except ClusterError as exc:
                notes.append(str(exc))
        self.db.update_app(app["id"], cluster=json.dumps(sorted(set(server_ids))) if server_ids else None)
        return notes

    def spread(self, app: dict, sha: str, rlog: ReleaseLog) -> Callable[[], list[str]]:
        """Ask every chosen server to build ``sha``; the returned function waits for the copies."""
        servers = [s for s in self.db.list_cluster_servers() if s["id"] in json.loads(app.get("cluster") or "[]")]
        try:  # the copies must never stop the deploy of this server
            gh = self.deployer.github()
            installation = app["installation_id"] or gh.installation_for(app["repo"])
            token, commit = gh.token(installation), gh.commit(installation, app["repo"], sha)
            env = decrypt_env(self._fernet(), app["env"])
        except Exception as exc:  # noqa: BLE001 - GitHub, the secret key: reported, the copies are skipped
            rlog.line(f"warning: no copy on the other servers this time ({exc})")
            return lambda: []
        spec = {"name": app["name"], "repo": app["repo"], "branch": app["branch"], "type": app["type"],
                "build_command": app["build_command"], "start_command": app["start_command"],
                "root_dir": app["root_dir"], "env": env}
        rlog.step(f"Building the copies on {', '.join(s['name'] for s in servers)}")
        results: dict[int, Any] = {}

        def one(server: dict) -> None:
            try:
                started = self._call(server, "PUT", f"/api/cluster/copies/{app['id']}", {
                    "leader": self.name(), "spec": spec, "sha": sha, "git_token": token,
                    "message": commit.get("message"), "author": commit.get("author")}, timeout=30)
                deadline = time.monotonic() + BUILD_TIMEOUT
                while time.monotonic() < deadline:
                    r = self._call(server, "GET", f"/api/cluster/copies/{app['id']}/releases/{started['release_id']}")
                    if r["status"] in ("live", "failed", "superseded"):
                        results[server["id"]] = (r["status"] == "live", r.get("error"), started["ports"])
                        return
                    time.sleep(3)
                results[server["id"]] = (False, "timed out", [])
            except (ClusterError, KeyError, TypeError) as exc:
                results[server["id"]] = (False, str(exc), [])

        threads = [threading.Thread(target=one, args=(s,), daemon=True) for s in servers]
        for t in threads:
            t.start()

        def wait() -> list[str]:
            for t in threads:
                t.join()
            backends = []
            for s in servers:
                ok, error, ports = results.get(s["id"], (False, "no answer", []))
                if ok and s["address"]:
                    found = [f"{s['address']}:{p}" for p in ports]
                    backends += found
                    rlog.line(f"{s['name']}: copy live on {', '.join(found)}")
                else:
                    rlog.line(f"warning: {s['name']}: copy not available ({error}); left out of the load balancer")
            return backends

        return wait

    # -- copies of an app of another server (this server is driven) ---------------------

    def put_copy(self, app_id: str, body: dict) -> dict:
        address = self.address()
        if not address:
            raise ClusterError("this server has no private address: set it on its Servers page")
        spec = body.get("spec") or {}
        source = _GivenSource(body.get("sha"), body.get("message"), body.get("author"), body.get("git_token"))
        app = self.db.get_app(app_id)
        if app and not app.get("replica_of"):
            raise ClusterError(f"an app {app_id} of this server already exists")
        app_type = ServiceType(spec.get("type", "unknown"))
        if app_type not in PROXY_TYPES:
            raise ClusterError("only Node.js, Next.js and Python apps can have copies")
        fields = {"name": spec.get("name") or app_id, "repo": spec["repo"], "branch": spec["branch"],
                  "type": app_type.value, "build_command": spec.get("build_command"),
                  "start_command": spec.get("start_command"), "root_dir": spec.get("root_dir") or "",
                  "env": encrypt_env(self._fernet(), spec.get("env") or {}) if spec.get("env") else None,
                  "bind": address, "replica_of": str(body.get("leader") or "another Mas")[:60], "auto_deploy": 0}
        if app is None:
            self.db.create_app(app_id, mode="managed", domain=None, installation_id=None,
                               port=allocate_port(self.host, self.config, self.db), **fields)
        else:
            self.db.update_app(app_id, **fields)
        app = self.db.get_app(app_id)
        rid = self.db.create_release(app_id, "cluster", fields["replica_of"], body.get("sha"))
        self.deployer.sources[rid] = source  # before the worker can pick the release up
        self.deployer.queue.put(rid)
        self.deployer._start_worker()
        return {"release_id": rid, "ports": [p for _, p in app_instances(app)]}

    def copy_release(self, app_id: str, release_id: int) -> dict:
        r = self.db.get_release(release_id)
        if r is None or r["app_id"] != app_id:
            raise ClusterError("unknown release")
        return {"status": r["status"], "error": r["error"]}

    def delete_copy(self, app_id: str) -> list[str]:
        app = self.db.get_app(app_id)
        if app is None:
            return []
        if not app.get("replica_of"):
            raise ClusterError(f"{app_id} is an app of this server, not a copy")
        try:
            return self.deployer.remove_app(app_id, purge_files=True)
        except DeployError as exc:
            raise ClusterError(str(exc)) from None


class _GivenSource:
    """Stands for the GitHub App when another Mas provides the commit and a clone token."""

    def __init__(self, sha: str | None, message: str | None, author: str | None, token: str | None) -> None:
        if not sha or not token:
            raise ClusterError("a copy needs the commit and a clone token")
        self.sha, self.message, self.author, self._token = sha, message or "", author, token

    def installation_for(self, repo: str) -> int:
        return 0

    def commit(self, installation_id: int, repo: str, ref: str) -> dict:
        return {"sha": self.sha, "message": self.message, "author": self.author}

    def token(self, installation_id: int) -> str:
        return self._token
