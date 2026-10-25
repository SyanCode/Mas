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
import logging
import os
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from cryptography.fernet import InvalidToken

from mas import __version__
from mas.config import Config, save_section
from mas.db import Database
from mas.deploy import PROXY_TYPES, DeployError, Deployer, ReleaseLog, allocate_port, app_instances
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import Host
from mas.models import ServiceType

log = logging.getLogger(__name__)

TOKEN_PREFIX = "masc_"
EPOCH_SETTING = "cluster_epoch"
PRIMARY_SETTING = "cluster_primary"
STATE_SETTING = "cluster_state"
PROMOTED_SETTING = "cluster_promoted_deploys"
ADDRESS_SETTING = "cluster_address"
NAME_SETTING = "cluster_name"
# Networks the copies may listen on: private ranges, CGNAT (Tailscale), IPv6 ULA.
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                   "100.64.0.0/10", "fc00::/7")]
BUILD_TIMEOUT = 1800
WINDOW_SHORT, WINDOW_LONG = 60, 900  # seconds: requests of the last minute / quarter of an hour
UPSTREAM_TAIL = 8 * 1024 * 1024      # read the end of the upstream log only
TRAFFIC_CACHE_S = 3                  # several open pages share one reading
FAILOVER_AFTER = 120                 # seconds a server may be silent before its copies move elsewhere


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
    """https://… (another Mas with its dashboard), or http://<private address>:<port> (an agent, reached
    over the private network: VPC, WireGuard, Tailscale)."""
    url = url.strip().rstrip("/")
    if " " in url or len(url) > 300:
        raise ClusterError("invalid address")
    if url.startswith("https://"):
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "http" and parsed.hostname and not parsed.path:
        try:
            validate_address(parsed.hostname)
            return url
        except ClusterError:
            pass
    raise ClusterError("use https://… (a Mas with its dashboard) or http://<private address>:7070 (an agent)")


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
        self._tails: dict[str, dict] = {}
        self.down_since: dict[int, float] = {}  # server id → monotonic time it stopped answering
        self._pushed: dict[int, str] = {}  # server id → digest of the last state it received
        self.state_sent: dict[int, float] = {}  # server id → when it last received the state
        self._traffic_cache: tuple[float, dict] | None = None

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
        return {"name": self.name(), "version": __version__, "address": self.address(), "role": self.config.cluster_role,
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
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   "X-Mas-Primary": f"{self.epoch()}:{self.name()}"}
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
            down = self.down_since.get(server["id"])
            out = {"id": server["id"], "name": server["name"], "url": server["url"], "address": server["address"],
                   "info": None, "error": None, "latency_ms": None,
                   "down_for_s": round(time.monotonic() - down) if down is not None else None,
                   "state_sent_at": self.state_sent.get(server["id"])}
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

    # -- taking over when the primary is gone (promotion) --------------------------------
    # The primary sends every server its state: apps and their settings, the servers and their join tokens,
    # its GitHub App and the dashboard accounts. A server keeps it encrypted with its own key, and
    # `mas agent promote` makes it the primary from that state. The epoch (+1 at each promotion) travels
    # with every call: a server refuses an older primary, so the former one cannot take back control.

    def epoch(self) -> int:
        try:
            return int(self.db.get_setting(EPOCH_SETTING) or 1)
        except ValueError:
            return 1

    def accept_primary(self, header: str | None) -> None:
        """On a driven server: refuse calls from a primary older than the latest one seen."""
        if not header or ":" not in header:
            return
        epoch_text, _, name = header.partition(":")
        try:
            epoch = int(epoch_text)
        except ValueError:
            return
        seen = self.epoch()
        if epoch < seen:
            raise ClusterError(f"{name} is not the primary anymore (a newer primary took over): "
                               "remove the servers from its Servers page or promote it again")
        if epoch > seen:
            self.db.set_setting(EPOCH_SETTING, str(epoch))
        self.db.set_setting(PRIMARY_SETTING, name[:60])

    def state(self) -> dict:
        fernet = self._fernet()
        servers = self.db.list_cluster_servers()
        urls = {s["id"]: s["url"] for s in servers}
        apps = []
        for a in self.db.list_apps():
            if a["mode"] != "managed" or a.get("replica_of"):
                continue  # adopted folders and ready-made services live on this server only
            apps.append({k: a.get(k) for k in ("id", "name", "repo", "branch", "type", "domain", "root_dir",
                                               "build_command", "start_command", "auto_deploy")}
                        | {"aliases": json.loads(a.get("aliases") or "[]"), "env": decrypt_env(fernet, a["env"]),
                           "cluster": [urls[i] for i in json.loads(a.get("cluster") or "[]") if i in urls]})
        github = None
        pem = self.host.read_text(self.config.github_private_key_path) if self.config.github_app_id else None
        if pem:
            github = {"app_id": self.config.github_app_id, "app_slug": self.config.github_app_slug,
                      "webhook_secret": self.config.github_webhook_secret, "private_key": pem}
        return {"epoch": self.epoch(), "primary": {"name": self.name(), "address": self.address()},
                "servers": [{"name": s["name"], "url": s["url"], "address": s["address"],
                             "token": decrypt_env(fernet, s["token"]).get("token")} for s in servers],
                "apps": apps, "github": github,
                "users": [{"username": u["username"], "password_hash": u["password_hash"],
                           "totp_secret": u["totp_secret"], "role": u["role"]} for u in self.db.list_users()],
                "letsencrypt_email": self.config.letsencrypt_email}

    def _push_state(self, servers: dict[int, dict]) -> None:
        state = self.state()
        digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        for server in servers.values():
            if self._pushed.get(server["id"]) == digest:
                continue
            try:
                self._call(server, "PUT", "/api/cluster/state", state, timeout=20)
                self._pushed[server["id"]] = digest
                self.state_sent[server["id"]] = time.time()
            except ClusterError as exc:
                log.warning("cluster state not sent: %s", exc)

    def store_state(self, state: dict) -> None:
        """On a driven server: keep the state of the primary, encrypted, for a promotion."""
        self.db.set_setting(STATE_SETTING, self._fernet().encrypt(json.dumps(state).encode()).decode())

    def stored_state(self) -> dict | None:
        raw = self.db.get_setting(STATE_SETTING)
        if not raw:
            return None
        try:
            return json.loads(self._fernet().decrypt(raw.encode()))
        except (InvalidToken, ValueError):
            return None

    def promote(self, force: bool = False, probe: Callable[[str], bool] | None = None) -> dict:
        """Make this server the primary, from the last state the former primary sent. Returns what was
        done and what is left to the person (DNS, dashboard)."""
        state = self.stored_state()
        if state is None:
            raise ClusterError("no state received from a primary yet: this server cannot take over")
        old = state.get("primary") or {}
        if not force and old.get("address") and (probe or _answers)(old["address"]):
            raise ClusterError(f"{old.get('name')} ({old['address']}) still answers: promote with --force only if "
                               "it is really out of service")
        fernet = self._fernet()
        notes: list[str] = []
        # 1. accounts of the dashboard, GitHub App
        for u in state.get("users") or []:
            if not self.db.get_user(u["username"]):
                uid = self.db.create_user(u["username"], u["password_hash"], u.get("role") or "admin")
                if u.get("totp_secret"):
                    self.db.update_user(uid, totp_secret=u["totp_secret"])
        gh = state.get("github")
        if gh:
            self.host.write_text(self.config.github_private_key_path, gh["private_key"], mode=0o600)
            save_section(self.host, "github", {"app_id": gh["app_id"], "app_slug": gh.get("app_slug"),
                                               "private_key_path": self.config.github_private_key_path,
                                               "webhook_secret": gh.get("webhook_secret")})
        else:
            notes.append("no GitHub App in the state: connect one in Settings → GitHub")
        if state.get("letsencrypt_email"):
            save_section(self.host, "deploy", {"letsencrypt_email": state["letsencrypt_email"]})
        # 2. the other servers (not this one, not the former primary)
        mine = self.address()
        for s in state.get("servers") or []:
            if s.get("address") in (mine, old.get("address")) or not s.get("token"):
                continue
            if not any(x["url"] == s["url"] for x in self.db.list_cluster_servers()):
                self.db.add_cluster_server(s["name"], s["url"], encrypt_env(fernet, {"token": s["token"]}),
                                           s.get("address"))
        ids = {s["url"]: s["id"] for s in self.db.list_cluster_servers()}
        # 3. the apps: a copy running here becomes the app itself; the others are created
        deploy = []
        for a in state.get("apps") or []:
            fields = {"name": a["name"], "repo": a["repo"], "branch": a["branch"], "type": a["type"],
                      "domain": a.get("domain"), "root_dir": a.get("root_dir") or "",
                      "build_command": a.get("build_command"), "start_command": a.get("start_command"),
                      "auto_deploy": int(a.get("auto_deploy") or 0), "installation_id": None,
                      "env": encrypt_env(fernet, a["env"]) if a.get("env") else None,
                      "aliases": json.dumps(a["aliases"]) if a.get("aliases") else None,
                      "cluster": json.dumps(sorted(ids[u] for u in a.get("cluster") or [] if u in ids)) or None,
                      "replica_of": None, "bind": None, "cluster_backends": None}
            if fields["cluster"] == "[]":
                fields["cluster"] = None
            existing = self.db.get_app(a["id"])
            if existing and not existing.get("replica_of"):
                notes.append(f"{a['id']}: an app of this server has the same id, kept as it is")
                continue
            if existing:
                self.db.update_app(a["id"], **fields)
            else:
                port = allocate_port(self.host, self.config, self.db) if ServiceType(a["type"]) in PROXY_TYPES \
                    else None
                self.db.create_app(a["id"], mode="managed", port=port, **fields)
            deploy.append(a["id"])
        self.db.set_setting(EPOCH_SETTING, str(int(state.get("epoch") or 1) + 1))
        self.db.set_setting(PROMOTED_SETTING, json.dumps(deploy))  # deployed when mas restarts as the primary
        save_section(self.host, "cluster", {"role": "primary", "address": mine})
        domains = sorted({d for a in state.get("apps") or [] for d in [a.get("domain"), *(a.get("aliases") or [])]
                          if d})
        return {"former_primary": old.get("name"), "apps": deploy, "domains": domains, "notes": notes,
                "epoch": self.epoch()}

    # -- keeping the copies up (auto-fill) ----------------------------------------------

    def watch(self, now: float | None = None) -> list[str]:
        """Probe the servers; when one that runs copies has not answered for FAILOVER_AFTER seconds,
        move its copies to an available server (the one with the most free memory) and redeploy the
        app, so the load balancer gets its capacity back. Called by the background loop of the primary."""
        now = now or time.monotonic()
        servers = self.db.list_cluster_servers()
        if not servers:
            return []
        healthy: dict[int, dict] = {}

        def probe(server: dict) -> tuple[dict, dict | None]:
            try:
                return server, self._call(server, "GET", "/api/cluster/self", timeout=5)
            except ClusterError:
                return server, None

        with ThreadPoolExecutor(max_workers=8) as pool:
            for server, info in pool.map(probe, servers):
                if info is None:
                    self.down_since.setdefault(server["id"], now)
                else:
                    if self.down_since.pop(server["id"], None) is not None:
                        self.db.audit("cluster", "server-back", True, detail=server["name"])
                    healthy[server["id"]] = info
        self._push_state({sid: next(s for s in servers if s["id"] == sid) for sid in healthy})
        notes = []
        apps = self.db.list_apps()
        # a server that came back may still run a copy that was moved elsewhere meanwhile: remove it
        for sid, info in healthy.items():
            server = next(s for s in servers if s["id"] == sid)
            for copy in info.get("apps") or []:
                owner = next((a for a in apps if a["id"] == copy["id"]), None)
                if copy.get("replica_of") == self.name() and owner is not None \
                        and sid not in json.loads(owner.get("cluster") or "[]"):
                    try:
                        self._call(server, "DELETE", f"/api/cluster/copies/{copy['id']}")
                        notes.append(f"{copy['id']}: old copy removed from {server['name']}")
                    except ClusterError as exc:
                        notes.append(str(exc))
        for app in apps:
            chosen = json.loads(app.get("cluster") or "[]")
            dead = [sid for sid in chosen if now - self.down_since.get(sid, now) >= FAILOVER_AFTER]
            if not dead:
                continue
            spare = sorted((sid for sid in healthy if sid not in chosen),
                           key=lambda sid: -(healthy[sid].get("memory", {}).get("available_mb") or 0))
            names = {s["id"]: s["name"] for s in servers}
            new = [sid for sid in chosen if sid not in dead] + spare[:len(dead)]
            moved = ", ".join(f"{names[d]} → {names[n]}" for d, n in zip(dead, spare))
            note = f"{app['id']}: {', '.join(names[d] for d in dead)} down" + \
                (f", copy moved ({moved})" if spare else ", no other server available: left out of the load balancer")
            self.db.update_app(app["id"], cluster=json.dumps(sorted(new)) if new else None)
            self.db.audit("cluster", "failover", bool(spare), service_id=app["id"], detail=note)
            self.deployer.enqueue(app["id"], "failover", "cluster")
            notes.append(note)
        return notes

    # -- live traffic (the cluster map) -------------------------------------------------

    def traffic(self, now: float | None = None) -> dict:
        """Requests of the last minute and the last 15 minutes, per app and per backend, read from the
        upstream logs nginx writes for every proxied app (mas_upstream format)."""
        now = now or time.time()
        if self._traffic_cache and now - self._traffic_cache[0] < TRAFFIC_CACHE_S:
            return self._traffic_cache[1]
        peers = {s["address"]: s for s in self.db.list_cluster_servers() if s["address"]}
        apps = []
        for app in self.db.list_apps():
            if app["mode"] != "managed" or ServiceType(app["type"]) not in PROXY_TYPES or not app.get("domain"):
                continue
            counts: dict[str, dict] = {}
            for at, addr, status, took in self._follow(app["id"], now):
                age = now - at
                if age > WINDOW_LONG or age < -5:  # lines are not always in time order
                    continue
                c = counts.setdefault(addr, {"minute": 0, "quarter": 0, "errors": 0, "time": 0.0})
                c["quarter"] += 1
                c["minute"] += age <= WINDOW_SHORT
                c["errors"] += status.startswith("5") or status == "-"
                c["time"] += took
            configured = self.deployer._upstream_servers(app)
            backends = []
            for addr in list(dict.fromkeys([*configured, *counts])):
                if addr == "-":
                    continue
                host_part = addr.rsplit(":", 1)[0]
                c = counts.get(addr, {"minute": 0, "quarter": 0, "errors": 0, "time": 0.0})
                server = "self" if host_part in ("127.0.0.1", "localhost", self.address()) else \
                    (peers[host_part]["id"] if host_part in peers else None)
                backends.append({"address": addr, "server": server, "configured": addr in configured,
                                 "requests_minute": c["minute"], "requests_15min": c["quarter"],
                                 "errors_15min": c["errors"],
                                 "avg_ms": round(c["time"] / c["quarter"] * 1000) if c["quarter"] else None})
            apps.append({"id": app["id"], "name": app["name"], "domain": app["domain"], "backends": backends})
        result = {"at": now, "window_s": [WINDOW_SHORT, WINDOW_LONG], "apps": apps}
        self._traffic_cache = (now, result)
        return result

    def _follow(self, app_id: str, now: float) -> deque:
        """The requests of the last 15 minutes of an app, kept in memory: only the lines nginx added
        since the previous call are read (the end of the log the first time, or after a rotation)."""
        path = f"/var/log/nginx/mas-app-{app_id}.upstream.log"
        state = self._tails.setdefault(app_id, {"inode": None, "offset": 0, "entries": deque()})
        fid = self.host.file_id(path)
        if fid is None:
            state["entries"].clear()
            return state["entries"]
        if fid[0] != state["inode"] or fid[1] < state["offset"]:  # first time, rotated or truncated
            data = self.host.read_tail(path, UPSTREAM_TAIL)
            state.update(inode=fid[0], offset=fid[1])
            state["entries"].clear()
        else:
            data = self.host.read_from(path, state["offset"], UPSTREAM_TAIL)
            cut = data.rfind(b"\n") + 1
            data = data[:cut]
            state["offset"] += cut
        entries = state["entries"]
        for line in data.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) < 4 or len(parts) % 2:
                continue
            try:
                at, took = float(parts[0]), float(parts[-1]) if parts[-1] != "-" else 0.0
            except ValueError:
                continue
            # "msec 10.0.0.2:7101, 127.0.0.1:7100 502, 200 0.31": a retry; the last backend answered
            tried = (len(parts) - 2) // 2
            entries.append((at, parts[tried].rstrip(","), parts[2 * tried].rstrip(","), took))
        while entries and now - entries[0][0] > WINDOW_LONG:
            entries.popleft()
        return entries

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


def _answers(address: str) -> bool:
    """Does a Mas still listen on that address (its dashboard port on the private network)?"""
    for port in (7070, 443, 80):
        try:
            with socket.create_connection((address, port), timeout=3):
                return True
        except OSError:
            continue
    return False


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
