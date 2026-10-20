"""Assemble collector outputs into ``Service`` objects.

Chain for a reverse-proxied site::

    nginx domain -> proxy_pass port -> ss -> PID -> cgroup (manager) + cwd (path)

PHP sites (``fastcgi_pass``) and static sites (``root`` only) are described by
nginx alone. Anything listening on a port, or managed by PM2/Docker/systemd,
without a vhost is reported too, with no domain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath

from mas.collectors import ScanContext
from mas.collectors import docker, nginx, pm2, ports, processes, systemd
from mas.collectors.docker import Container
from mas.collectors.nginx import NginxSite, Target
from mas.collectors.pm2 import Pm2App
from mas.collectors.ports import ListeningSocket
from mas.collectors.processes import ProcessInfo, ProcessTable, UserEntry
from mas.collectors.systemd import SystemdUnit
from mas.config import Config, load_config
from mas.detect_type import detect_type
from mas.gitinfo import read_git
from mas.host import Host
from mas.models import Manager, ScanResult, Service, ServiceType, Status

log = logging.getLogger(__name__)

# Local DNS stubs are never user services.
IGNORED_ADDRESSES = {"127.0.0.53", "127.0.0.54"}

SYSTEM_PATH_PREFIXES = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/run/", "/etc/",
                        "/var/run/", "/var/lib/", "/dev/", "/proc/", "/sys/")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", value.lower()).strip("-.") or "service"


@dataclass
class Backend:
    """The process (or container) answering behind a port or socket."""

    key: str
    manager: Manager
    name: str
    status: Status
    pid: int | None = None
    port: int | None = None
    path: str | None = None
    user: str | None = None
    unit: str | None = None
    container_id: str | None = None
    cmdline: list[str] = field(default_factory=list)
    log_paths: list[str] = field(default_factory=list)


class Correlator:
    def __init__(
        self,
        ctx: ScanContext,
        users: dict[int, UserEntry],
        sites: list[NginxSite],
        sockets: list[ListeningSocket],
        unix_sockets: list[ListeningSocket],
        units: list[SystemdUnit],
        apps: list[Pm2App],
        containers: list[Container],
    ) -> None:
        self.ctx = ctx
        self.host = ctx.host
        self.config = ctx.config
        self.procs = ProcessTable(ctx, users)
        self.sites = sites
        self.sockets = sockets
        self.units = {u.name: u for u in units}
        self.apps = apps
        self.containers = containers

        self.by_port: dict[int, ListeningSocket] = {}
        for s in sockets:
            if s.port is not None:
                self.by_port.setdefault(s.port, s)
        self.unix_by_path = {s.address: s for s in unix_sockets}
        self.docker_by_port = {p: c for c in containers for p in c.public_ports}
        self.docker_by_id = {c.id: c for c in containers}
        self.pm2_by_pid = {a.pid: a for a in apps if a.pid}
        self.nginx_running: bool | None = (
            any(s.process == "nginx" for s in sockets) if sockets else None
        )

        self.services: dict[str, Service] = {}
        self.claimed: dict[str, str] = {}  # backend key -> service id

    # -- backends --------------------------------------------------------

    def _from_container(self, c: Container) -> Backend:
        return Backend(
            key=f"docker:{c.id}", manager=Manager.DOCKER, name=c.name, status=Status(c.status),
            port=c.public_ports[0] if c.public_ports else None, path=c.working_dir,
            unit=c.name, container_id=c.id,
        )

    def _from_pm2(self, a: Pm2App, proc: ProcessInfo | None = None) -> Backend:
        proc = proc or self.procs.get(a.pid)
        return Backend(
            key=f"pm2:{a.user}:{a.name}", manager=Manager.PM2, name=a.name, status=Status(a.status),
            pid=a.pid, port=a.port, path=a.cwd or (proc.cwd if proc else None), user=a.user,
            unit=a.name, cmdline=proc.cmdline if proc else [], log_paths=list(a.log_paths or []),
        )

    def _from_unit(self, u: SystemdUnit, proc: ProcessInfo | None = None) -> Backend:
        proc = proc or self.procs.get(u.main_pid)
        path = u.working_directory or (proc.cwd if proc else None)
        return Backend(
            key=f"systemd:{u.name}", manager=Manager.SYSTEMD, name=u.name.removesuffix(".service"),
            status=Status.RUNNING if u.sub == "running" else Status.UNKNOWN,
            pid=u.main_pid or (proc.pid if proc else None), path=None if path == "/" else path,
            user=u.user or (proc.user if proc else None), unit=u.name,
            cmdline=proc.cmdline if proc else [],
        )

    def backend_for_pid(self, pid: int, port: int | None = None) -> Backend:
        chain = self.procs.ancestors(pid)
        for p in chain:
            if p.pid in self.pm2_by_pid:
                return self._from_pm2(self.pm2_by_pid[p.pid], chain[0])
        if not chain:
            return Backend(f"pid:{pid}", Manager.UNKNOWN, f"pid {pid}", Status.RUNNING, pid=pid, port=port)
        proc = chain[0]
        if proc.container_id and proc.container_id in self.docker_by_id:
            return self._from_container(self.docker_by_id[proc.container_id])
        if proc.pm2_app:
            app = next((a for a in self.apps if a.name == proc.pm2_app and a.user == proc.user), None)
            if app:
                return self._from_pm2(app, proc)
        if proc.systemd_unit:
            unit = self.units.get(proc.systemd_unit) or SystemdUnit(name=proc.systemd_unit)
            backend = self._from_unit(unit, self.procs.get(unit.main_pid) or proc)
            backend.port = port
            return backend
        # Unmanaged process: key on the top of the chain of same-named processes (master).
        top = proc
        for parent in chain[1:]:
            if parent.comm != proc.comm:
                break
            top = parent
        cwd = top.cwd if top.cwd and top.cwd != "/" else None
        return Backend(
            key=f"pid:{top.pid}", manager=Manager.UNKNOWN, name=top.comm or f"pid {top.pid}",
            status=Status.RUNNING, pid=top.pid, port=port, path=cwd, user=top.user,
            cmdline=top.cmdline,
        )

    def resolve_target(self, target: Target) -> Backend:
        if target.kind == "tcp" and target.is_local and target.port:
            port = target.port
            if port in self.docker_by_port:
                backend = self._from_container(self.docker_by_port[port])
                backend.port = port  # the port nginx uses, not the lowest published one
                return backend
            sock = self.by_port.get(port)
            if sock is None:
                # Nothing listens: the app behind the vhost is down (or ss failed).
                status = Status.STOPPED if self.sockets else Status.UNKNOWN
                return Backend(f"port:{port}", Manager.UNKNOWN, f"port {port}", status, port=port)
            if sock.pid is None:
                return Backend(f"port:{port}", Manager.UNKNOWN, sock.process or f"port {port}",
                               Status.RUNNING, port=port)
            backend = self.backend_for_pid(sock.pid, port)
            backend.port = backend.port or port
            return backend
        if target.kind == "unix" and target.socket:
            sock = self.unix_by_path.get(target.socket)
            if sock and sock.pid:
                return self.backend_for_pid(sock.pid)
            status = Status.RUNNING if self.host.exists(target.socket) else Status.STOPPED
            return Backend(f"unix:{target.socket}", Manager.UNKNOWN,
                           PurePosixPath(target.socket).name, status)
        if target.kind == "tcp":
            return Backend(f"remote:{target.host}:{target.port}", Manager.UNKNOWN,
                           f"{target.host}:{target.port}", Status.UNKNOWN)
        return Backend(f"dynamic:{target.raw}", Manager.UNKNOWN, target.raw, Status.UNKNOWN)

    # -- services --------------------------------------------------------

    def _unique_id(self, base: str) -> str:
        sid, n = slugify(base), 2
        while sid in self.services:
            sid = f"{slugify(base)}-{n}"
            n += 1
        return sid

    def _add(self, service: Service, backend_key: str | None = None) -> Service:
        self.services[service.id] = service
        if backend_key:
            self.claimed[backend_key] = service.id
        return service

    def _service_from_backend(self, sid: str, name: str, b: Backend, **extra) -> Service:
        svc = Service(
            id=sid, name=name, manager=b.manager, path=b.path, port=b.port, pid=b.pid,
            user=b.user, status=b.status, unit=b.unit, container_id=b.container_id,
            cmdline=" ".join(b.cmdline) or None, log_paths=list(b.log_paths), **extra,
        )
        svc.type = detect_type(self.host, b.path, proxy=True, cmdline=b.cmdline)
        return svc

    def _site_common(self, site: NginxSite) -> dict:
        return dict(
            domains=list(site.names), ssl_expires=site.ssl_expires,
            ssl_certificate=site.ssl_certificate, https_redirect=site.https_redirect,
            nginx_files=list(site.files),
        )

    def _site_id(self, site: NginxSite) -> str:
        if site.primary_name:
            return self._unique_id(site.primary_name)
        return self._unique_id(f"nginx-{PurePosixPath(site.files[0]).name}")

    def _nginx_status(self) -> Status:
        if self.nginx_running is None:
            return Status.UNKNOWN
        return Status.RUNNING if self.nginx_running else Status.STOPPED

    def add_site(self, site: NginxSite) -> None:
        label = site.primary_name or "(default)"
        name = site.primary_name or f"nginx default ({PurePosixPath(site.files[0]).name})"

        if site.proxy:
            primary = next((r for r in site.proxy if r.location == "/"), site.proxy[0])
            ordered = [primary] + [r for r in site.proxy if r is not primary]
            for route in ordered:
                b = self.resolve_target(route.target)
                route_label = f"{label}{route.location}"
                if b.key in self.claimed:
                    svc = self.services[self.claimed[b.key]]
                    svc.routes.append(route_label)
                    if not svc.domains:
                        svc.domains = site.names[:1]
                    continue
                if route is primary:
                    svc = self._service_from_backend(
                        self._site_id(site), name, b, upstream=route.target.raw,
                        routes=[route_label], **self._site_common(site),
                    )
                else:
                    svc = self._service_from_backend(
                        self._unique_id(f"{b.manager.value}-{b.name}"), b.name, b,
                        upstream=route.target.raw, routes=[route_label],
                        domains=site.names[:1], nginx_files=list(site.files),
                    )
                self._add(svc, b.key)
            return

        if site.fastcgi:
            target = site.fastcgi[0].target
            sock = None
            if target.kind == "unix" and target.socket:
                sock = self.unix_by_path.get(target.socket)
                up = sock is not None or self.host.exists(target.socket)
            else:
                sock = self.by_port.get(target.port or 0)
                up = sock is not None
            pid = sock.pid if sock else None
            proc = self.procs.get(pid)
            svc = Service(
                id=self._site_id(site), name=name, manager=Manager.PHP_FPM, path=site.root,
                pid=pid, status=Status.RUNNING if up else Status.STOPPED,
                unit=proc.systemd_unit if proc else None, upstream=target.raw,
                routes=[f"{label}/"], log_paths=list(site.log_paths), **self._site_common(site),
            )
            svc.type = detect_type(self.host, site.root, fastcgi=True)
            self._add(svc)
            return

        svc = Service(
            id=self._site_id(site), name=name, manager=Manager.NGINX_STATIC, path=site.root,
            status=self._nginx_status(), upstream=site.redirect_to, unit="nginx.service",
            log_paths=list(site.log_paths), **self._site_common(site),
        )
        svc.type = (
            detect_type(self.host, site.root, static_root=True) if site.root else ServiceType.UNKNOWN
        )
        self._add(svc)

    def add_unclaimed(self, b: Backend) -> None:
        if b.key in self.claimed:
            svc = self.services[self.claimed[b.key]]
            if svc.port is None and b.port is not None:
                svc.port = b.port
            return
        svc = self._service_from_backend(self._unique_id(f"{b.manager.value}-{b.name}"), b.name, b)
        self._add(svc, b.key)

    def run(self) -> list[Service]:
        for site in self.sites:
            self.add_site(site)

        for c in self.containers:
            self.add_unclaimed(self._from_container(c))
        for a in self.apps:
            self.add_unclaimed(self._from_pm2(a))
        for s in self.sockets:
            if s.port in self.docker_by_port or s.address in IGNORED_ADDRESSES:
                continue
            if self.config.process_excluded(s.process):
                continue
            if s.pid is None:
                b = Backend(f"port:{s.port}", Manager.UNKNOWN, s.process or f"port {s.port}",
                            Status.RUNNING, port=s.port)
            else:
                b = self.backend_for_pid(s.pid, s.port)
            if b.manager == Manager.SYSTEMD and b.unit and self.config.unit_excluded(b.unit):
                continue
            self.add_unclaimed(b)
        for u in self.units.values():
            if f"systemd:{u.name}" not in self.claimed and self._is_os_daemon(u):
                continue
            self.add_unclaimed(self._from_unit(u))

        for svc in self.services.values():
            self._enrich_git(svc)
        return sorted(self.services.values(), key=lambda s: (not s.domains, s.name.lower()))

    def _is_os_daemon(self, u: SystemdUnit) -> bool:
        """A unit with no port (checked by the caller), no working directory and a
        command that only touches system paths (colord, smartd, preload...)."""
        if not self.config.hide_os_daemons or not u.exec_start:
            return False
        if u.working_directory and u.working_directory != "/":
            return False
        paths = re.findall(r"(?:^|[\s=:])(/[^\s;'\"]+)", u.exec_start)
        return bool(paths) and all(p.startswith(SYSTEM_PATH_PREFIXES) for p in paths)

    def _enrich_git(self, svc: Service) -> None:
        try:
            info = read_git(self.host, svc.path)
        except Exception as exc:  # malformed repo must not break the scan
            self.ctx.warn(f"git: cannot read repository of {svc.id}: {exc}")
            return
        if info:
            svc.git_remote, svc.git_branch, svc.last_commit = info.remote, info.branch, info.commit


def scan(
    host: Host | None = None,
    config: Config | None = None,
    type_overrides: dict[str, ServiceType] | None = None,
) -> ScanResult:
    """Run every collector and correlate. Read-only."""
    host = host or Host()
    ctx = ScanContext(host, config or load_config(host))

    def safe(name: str, fn, *args):
        try:
            return fn(ctx, *args)
        except Exception as exc:  # a collector bug must not kill the scan
            log.exception("collector %s crashed", name)
            ctx.warn(f"{name}: collector failed ({exc})")
            return []

    users = processes.load_users(host)
    correlator = Correlator(
        ctx,
        users,
        sites=safe("nginx", nginx.collect),
        sockets=safe("ports", ports.collect),
        unix_sockets=safe("ports", ports.collect_unix),
        units=safe("systemd", systemd.collect),
        apps=safe("pm2", pm2.collect, users),
        containers=safe("docker", docker.collect),
    )
    services = correlator.run()
    for svc in services:
        if type_overrides and svc.id in type_overrides:
            svc.type, svc.type_source = type_overrides[svc.id], "manual"
    return ScanResult(scanned_at=datetime.now(timezone.utc), services=services, warnings=ctx.warnings)
