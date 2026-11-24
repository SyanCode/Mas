"""nginx behind Caddy or Apache: for servers where one of them already holds ports 80 and 443.

The front server keeps the web ports and HTTPS (Caddy gets its certificates by itself; Apache through certbot's
apache plugin). For each address of Mas it gets one small site sending everything to the nginx of Mas, which then
listens on 127.0.0.1:<port> only, without TLS. Everything Mas does in nginx (maintenance, redirects, sitemaps, sleep
and wake-up, statistics, previews) keeps working unchanged; the real visitor address and scheme come from the
X-Forwarded-* headers of the front server (``REALIP_PATH``).

Mas only writes its own files: ``/etc/caddy/mas/<id>.caddy`` (imported once from the Caddyfile), or
``mas-<id>.conf`` in Apache's sites folder. Each change is checked (``caddy validate`` / ``apachectl configtest``)
before the reload, and the previous file is put back if the check fails.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import PurePosixPath

from mas.host import Host

SETTING = "web_front"  # {"server": "caddy" | "apache", "port": 8088}
SERVERS = ("caddy", "apache")
CADDYFILE = "/etc/caddy/Caddyfile"
CADDY_DIR = "/etc/caddy/mas"
CADDY_IMPORT = f"import {CADDY_DIR}/*.caddy"
REALIP_PATH = "/etc/nginx/conf.d/mas-front.conf"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9*]([A-Za-z0-9.-]*[A-Za-z0-9])?$")


class FrontError(Exception):
    pass


REALIP = """# Written by Mas: nginx behind {server} (mas.front). The visitor's address and scheme come from its headers.
set_real_ip_from 127.0.0.1;
set_real_ip_from ::1;
{header}real_ip_recursive on;
map $http_x_forwarded_proto $mas_forwarded_proto {{
    default $http_x_forwarded_proto;
    "" $scheme;
}}
"""


def behind(content: str, port: int) -> str:
    """An nginx site of Mas made for life behind the front server: local port only, no HTTPS, the scheme the
    visitor used passed on to the app."""
    content = re.sub(r"(?m)^    listen \[::\]:80;\n", "", content)
    content = re.sub(r"(?m)^    listen 80;$", f"    listen 127.0.0.1:{port};", content)
    return content.replace("X-Forwarded-Proto $scheme", "X-Forwarded-Proto $mas_forwarded_proto")


def caddy_site(names: list[str], port: int) -> str:
    return (f"# Written by Mas: {names[0]} → its nginx. Changes are overwritten.\n"
            f"{', '.join(names)} {{\n"
            f"    reverse_proxy 127.0.0.1:{port}\n"
            "}\n")


def apache_site(names: list[str], port: int, cert: str | None) -> str:
    """Port 80 proxies until a certificate exists (certbot's apache plugin answers its challenge there); then
    80 redirects to 443, which proxies (websockets included)."""
    alias = f"    ServerAlias {' '.join(names[1:])}\n" if len(names) > 1 else ""
    proxy = (f"    ProxyPreserveHost On\n"
             f"    ProxyPass / http://127.0.0.1:{port}/ upgrade=websocket\n"
             f"    ProxyPassReverse / http://127.0.0.1:{port}/\n")
    head = f"# Written by Mas: {names[0]} → its nginx. Changes are overwritten.\n"
    if not cert:
        return (head + f"<VirtualHost *:80>\n    ServerName {names[0]}\n{alias}"
                f"    RequestHeader set X-Forwarded-Proto \"http\"\n{proxy}</VirtualHost>\n")
    return (head + f"<VirtualHost *:80>\n    ServerName {names[0]}\n{alias}"
            f"    Redirect permanent / https://{names[0]}/\n</VirtualHost>\n"
            f"<VirtualHost *:443>\n    ServerName {names[0]}\n{alias}"
            f"    SSLEngine on\n    SSLCertificateFile {cert}/fullchain.pem\n    SSLCertificateKeyFile {cert}/privkey.pem\n"
            f"    RequestHeader set X-Forwarded-Proto \"https\"\n{proxy}</VirtualHost>\n")


class Front:
    def __init__(self, host: Host, db) -> None:
        self.host, self.db = host, db

    # -- settings --------------------------------------------------------------------------------------

    def get(self) -> dict | None:
        raw = self.db.get_setting(SETTING)
        cfg = json.loads(raw) if raw else None
        return cfg if cfg and cfg.get("server") in SERVERS else None

    def port(self) -> int | None:
        cfg = self.get()
        return int(cfg["port"]) if cfg else None

    def configure(self, server: str | None, port: int = 8088, mas_port: int = 7070) -> dict | None:
        if server in (None, "", "off", "nginx"):
            self.db.set_setting(SETTING, None)
            self.host.remove(REALIP_PATH)
            return None
        if server not in SERVERS:
            raise FrontError("the front server is caddy or apache")
        if not 1024 <= int(port) <= 65535 or int(port) in (7070,):
            raise FrontError("port of nginx behind the front server: 1024-65535 (not the one of Mas)")
        if server == "caddy" and not self.host.exists(CADDYFILE):
            raise FrontError(f"Caddy is not installed here ({CADDYFILE} not found)")
        if server == "apache" and not self._apache_dir():
            raise FrontError("Apache is not installed here (no /etc/apache2 nor /etc/httpd)")
        cfg = {"server": server, "port": int(port), "target": int(mas_port)}  # the dashboard goes straight to Mas
        self.db.set_setting(SETTING, json.dumps(cfg))
        cdn = self.host.exists("/etc/nginx/conf.d/mas-realip.conf")  # Cloudflare already sets real_ip_header
        self.host.write_text(REALIP_PATH, REALIP.format(
            server=server, header="" if cdn else "real_ip_header X-Forwarded-For;\n"))
        return cfg

    # -- the front server's files ----------------------------------------------------------------------

    def _apache_dir(self) -> str | None:
        """Where Apache reads sites: Debian/Ubuntu/openSUSE (apache2), Fedora/RHEL/Arch (httpd)."""
        if self.host.is_dir("/etc/apache2/sites-enabled"):
            return "/etc/apache2/sites-enabled"
        if self.host.is_dir("/etc/apache2/vhosts.d"):
            return "/etc/apache2/vhosts.d"
        if self.host.is_dir("/etc/httpd/conf.d"):
            return "/etc/httpd/conf.d"
        return None

    def path(self, site_id: str) -> str:
        cfg = self.get() or {}
        if cfg.get("server") == "caddy":
            return f"{CADDY_DIR}/{site_id}.caddy"
        return f"{self._apache_dir() or '/etc/httpd/conf.d'}/mas-{site_id}.conf"

    def own_files(self) -> set[str]:
        cfg = self.get() or {}
        folder = CADDY_DIR if cfg.get("server") == "caddy" else self._apache_dir()
        if not folder:
            return set()
        return {f for f in self.host.glob(f"{folder}/*") if PurePosixPath(f).name.startswith(("mas-",))
                or folder == CADDY_DIR}

    def _test(self, server: str) -> tuple[bool, str]:
        tests = ([["caddy", "validate", "--config", CADDYFILE, "--adapter", "caddyfile"]] if server == "caddy"
                 else [["apachectl", "configtest"], ["apache2ctl", "configtest"]])
        for argv in tests:
            res = self.host.run(argv, timeout=60)
            if res is not None:
                return res.ok, (res.stderr + res.stdout).strip()[-1500:]
        return False, f"{tests[0][0]} not found"

    def _reload(self, server: str) -> None:
        unit = "caddy" if server == "caddy" else ("apache2" if self.host.is_dir("/etc/apache2") else "httpd")
        self.host.run(["systemctl", "reload", unit], timeout=60)

    def _apply(self, server: str, path: str, content: str | None) -> None:
        """Write (or remove, with None) one file, check, reload; the previous state back if the check fails."""
        old = self.host.read_text(path)
        if old == content:
            return
        if server == "caddy":
            self._ensure_import()
        if content is None:
            self.host.remove(path)
        else:
            self.host.makedirs(str(PurePosixPath(path).parent))
            self.host.write_text(path, content, mode=0o644)
        ok, detail = self._test(server)
        if not ok:
            if old is None:
                self.host.remove(path)
            else:
                self.host.write_text(path, old, mode=0o644)
            raise FrontError(f"{server} refused the configuration, nothing changed:\n{detail}")
        self._reload(server)

    def _ensure_import(self) -> None:
        text = self.host.read_text(CADDYFILE) or ""
        if CADDY_IMPORT in text:
            return
        self.host.makedirs(CADDY_DIR)
        self.host.write_text(f"{CADDYFILE}.mas-bak-{time.strftime('%Y%m%d%H%M%S')}", text, mode=0o644)
        self.host.write_text(CADDYFILE, text.rstrip("\n") + f"\n\n# The sites of Mas (mas.front)\n{CADDY_IMPORT}\n", mode=0o644)

    def site(self, site_id: str, names: list[str], cert: str | None = None) -> str | None:
        """The front site of one app (or the dashboard): its names → the nginx of Mas. Returns its file."""
        cfg = self.get()
        if cfg is None or not names:
            return None
        if not ID_RE.fullmatch(site_id) or not all(NAME_RE.fullmatch(n) for n in names):
            raise FrontError("invalid site name")
        target = cfg.get("target") if site_id == "dashboard" else None
        port = int(target or cfg["port"])
        content = caddy_site(names, port) if cfg["server"] == "caddy" else apache_site(names, port, cert)
        path = self.path(site_id)
        self._apply(cfg["server"], path, content)
        return path

    def remove(self, site_id: str) -> None:
        cfg = self.get()
        if cfg is not None and ID_RE.fullmatch(site_id):
            self._apply(cfg["server"], self.path(site_id), None)

    def conflict(self, name: str) -> str | None:
        """A site of the front server not written by Mas that already serves ``name``."""
        from mas.collectors import ScanContext
        from mas.collectors.webservers import collect_apache, collect_caddy
        cfg = self.get()
        if cfg is None:
            return None
        sites = collect_caddy(ScanContext(self.host)) if cfg["server"] == "caddy" else collect_apache(ScanContext(self.host))
        ours = self.own_files()
        for s in sites:
            if name in s.names and not set(s.files) <= ours:
                return ", ".join(f for f in s.files if f not in ours)
        return None

    def certificate(self, names: list[str], email: str | None, run) -> bool:
        """Caddy: nothing to do (automatic HTTPS). Apache: certbot's apache plugin, through ``run(argv)`` → rc."""
        cfg = self.get()
        if cfg is None or cfg["server"] == "caddy":
            return False
        contact = ["-m", email] if email else ["--register-unsafely-without-email"]
        return run(["certbot", "certonly", "--apache", "--non-interactive", "--agree-tos", *contact,
                    "--cert-name", names[0], "--expand", *(a for n in names for a in ("-d", n))]) == 0


# -- switching ------------------------------------------------------------------------------------------


DASHBOARD_FILES = ("/etc/nginx/sites-enabled/mas.conf", "/etc/nginx/conf.d/mas.conf")
OFF_SUFFIX = ".mas-front-off"


def _ours(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith("mas-") or path in DASHBOARD_FILES or name == "mas.conf"


def blocking_sites(host: Host) -> list[str]:
    """nginx sites that are not Mas's and listen on 80 or 443: behind a front server nginx cannot keep them."""
    from mas.collectors import ScanContext
    from mas.collectors.nginx import collect
    out = []
    for site in collect(ScanContext(host)):
        if set(site.ports) & {80, 443} and not all(_ours(f) for f in site.files):
            out.append(f"{site.primary_name or '(default)'} ({', '.join(f for f in site.files if not _ours(f))})")
    return out


def dashboard_domain(host: Host) -> tuple[str | None, str | None]:
    """(domain, file) of the nginx site of the dashboard, written by the installer."""
    for path in DASHBOARD_FILES:
        text = host.read_text(path) or host.read_text(path + OFF_SUFFIX)
        if text:
            m = re.search(r"server_name\s+([^;\s]+);", text)
            return (m.group(1) if m else None), path
    return None, None


def switch(front: Front, deployer, supabase, server: str | None, port: int, mas_port: int, log=print) -> list[str]:
    """Put nginx behind Caddy / Apache (``server``), or give it the web ports back (None). Every site of Mas is
    rewritten (nginx and the front server), the dashboard included. Returns what was done."""
    from mas.deploy import ServiceType, app_paths
    done: list[str] = []
    if server:
        blocking = blocking_sites(front.host)
        if blocking:
            raise FrontError("nginx also serves sites that are not apps of Mas on ports 80/443: "
                             f"{'; '.join(blocking)}. Move them to apps of Mas (or to {server}) first.")
    before = front.get()
    old_files = front.own_files() if before else set()
    front.configure(server, port, mas_port)
    domain, path = dashboard_domain(front.host)
    try:
        if server and domain:
            front.site("dashboard", [domain])
            if front.host.exists(path):  # its nginx site would listen on 443 next to the front server
                front.host.rename(path, path + OFF_SUFFIX)
            done.append(f"dashboard {domain}: served by {server} → Mas")
        elif not server and domain and front.host.exists(path + OFF_SUFFIX):
            front.host.rename(path + OFF_SUFFIX, path)
            done.append(f"dashboard {domain}: served by nginx again")
        log_path = deployer.data_dir / "logs" / "front.log"
        from mas.deploy import ReleaseLog
        rlog = ReleaseLog(log_path)
        try:
            for app in deployer.db.list_apps():
                if not app.get("domain") or app["mode"] not in ("managed", "stack") or app.get("replica_of"):
                    continue
                app_type = ServiceType.NODE if app["mode"] == "stack" else ServiceType(app["type"])
                paths = app_paths(deployer.config, app["id"])
                if not front.host.exists(paths.current) and app["mode"] == "managed":
                    continue
                if not server and not deployer._has_cert(app["domain"]):
                    deployer._ensure_certificate(app, rlog)  # nginx serves HTTPS itself again
                deployer._install_vhost(app, app_type, paths, rlog)
                done.append(f"{app['id']}: {app['domain']}")
            if supabase is not None:
                supabase.refresh_vhost()
        finally:
            rlog.close()
        if before and not server:  # the front server's files of Mas are not needed anymore
            for f in sorted(old_files):
                front.host.remove(f)
            if front._test(before["server"])[0]:
                front._reload(before["server"])
            done.append(f"{len(old_files)} site(s) of Mas removed from {before['server']}: stop it, or move its "
                        "own sites off ports 80/443, so that nginx can listen there again")
        test = front.host.run(["nginx", "-t"], timeout=60)
        if test is None or not test.ok:
            raise FrontError("nginx -t failed after the switch: " + ((test.stderr if test else "nginx not found") or "")[-800:])
        front.host.run(["systemctl", "reload", "nginx"], timeout=60)
    except Exception:
        front.db.set_setting(SETTING, json.dumps(before) if before else None)
        raise
    deployer.db.audit("mas", "web-front", True, detail=f"{server or 'nginx'}: {len(done)} site(s)")
    log(f"nginx {'behind ' + server + f' on 127.0.0.1:{port}' if server else 'on ports 80 and 443'}")
    return done
