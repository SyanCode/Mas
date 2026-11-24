"""Sites served by Apache (httpd) and Caddy, read from their configuration files, as the nginx collector does.

They become the same ``NginxSite`` records (with ``server`` set to "apache" or "caddy"), so the correlator turns them
into services the same way: a reverse proxy becomes the app behind it, a PHP site its PHP-FPM, the rest a static site.
Read-only: Mas never writes their configuration (it deploys its own apps behind nginx).

- Apache: Debian/Ubuntu ``/etc/apache2/apache2.conf``, Fedora/RHEL ``/etc/httpd/conf/httpd.conf``, openSUSE
  ``/etc/apache2/httpd.conf``, Arch ``/etc/httpd/conf/httpd.conf``, following ``Include`` / ``IncludeOptional``.
- Caddy: ``/etc/caddy/Caddyfile`` and its ``import``s. Caddy gets its certificates by itself: they are read from its
  data folder for the expiry date.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

from mas.collectors import ScanContext
from mas.collectors.nginx import NginxSite, Route, Target, parse_target, read_cert_expiry

APACHE_MAIN = ("/etc/apache2/apache2.conf", "/etc/httpd/conf/httpd.conf", "/etc/apache2/httpd.conf")
CADDYFILE = "/etc/caddy/Caddyfile"
CADDY_CERTS = ("/var/lib/caddy/.local/share/caddy/certificates", "/var/lib/caddy/certificates",
               "/root/.local/share/caddy/certificates")
MAX_DEPTH = 8
MAX_FILES = 300


def _merge(sites: list[NginxSite]) -> list[NginxSite]:
    """One site per name: the :80 and :443 blocks of a domain are the same site."""
    out: dict[str, NginxSite] = {}
    for s in sites:
        key = s.primary_name or f"{s.files[0]}#{len(out)}"
        if key not in out:
            out[key] = s
            continue
        m = out[key]
        m.names = list(dict.fromkeys(m.names + s.names))
        m.files = list(dict.fromkeys(m.files + s.files))
        m.ports = sorted(set(m.ports + s.ports))
        m.ssl = m.ssl or s.ssl
        m.https_redirect = m.https_redirect or s.https_redirect
        m.root = m.root or s.root
        m.proxy = m.proxy or s.proxy
        m.fastcgi = m.fastcgi or s.fastcgi
        m.ssl_certificate = m.ssl_certificate or s.ssl_certificate
        m.log_paths = list(dict.fromkeys(m.log_paths + s.log_paths))
        if m.proxy or m.fastcgi or m.root:
            m.redirect_to = None
    return list(out.values())


# -- Apache --------------------------------------------------------------------------------------------


def _apache_files(ctx: ScanContext, path: str, root: str, depth: int, seen: set[str]) -> list[tuple[str, str]]:
    """(file, text) of a configuration file and everything it includes, in order."""
    if depth > MAX_DEPTH or path in seen or len(seen) > MAX_FILES:
        return []
    seen.add(path)
    text = ctx.host.read_text(path)
    if text is None:
        return []
    out = [(path, text)]
    for m in re.finditer(r"(?im)^\s*Include(?:Optional)?\s+(\S+)", text):
        pattern = m.group(1).strip("\"'")
        if not pattern.startswith("/"):
            pattern = f"{root}/{pattern}"
        for f in sorted(ctx.host.glob(pattern)):
            out += _apache_files(ctx, f, root, depth + 1, seen)
    return out


def _apache_env(text: str) -> str:
    return text.replace("${APACHE_LOG_DIR}", "/var/log/apache2")


def parse_apache(files: list[tuple[str, str]]) -> list[NginxSite]:
    sites: list[NginxSite] = []
    for path, text in files:
        for m in re.finditer(r"(?is)<VirtualHost\s+([^>]*)>(.*?)</VirtualHost>", text):
            addresses, body = m.group(1), _apache_env(m.group(2))
            ports = sorted({int(p) for p in re.findall(r":(\d+)", addresses)}) or [80]
            names: list[str] = []
            site = NginxSite(names=names, files=[path], ports=ports, server="apache")
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    words = shlex.split(line, posix=True)
                except ValueError:
                    words = line.split()
                key, args = words[0].lower(), words[1:]
                if key == "servername" and args:
                    names.insert(0, args[0].split(":")[0].lower())
                elif key == "serveralias":
                    names += [a.lower() for a in args if "*" not in a]
                elif key == "documentroot" and args:
                    site.root = args[0].rstrip("/") or "/"
                elif key == "sslengine" and args and args[0].lower() == "on":
                    site.ssl = True
                elif key == "sslcertificatefile" and args:
                    site.ssl_certificate = args[0]
                elif key == "proxypass" and len(args) >= 2 and args[1] != "!":
                    target = args[1]
                    if target.startswith("unix:"):  # ProxyPass / unix:/run/app.sock|http://localhost/
                        site.proxy.append(Route(args[0], Target(target, "unix", socket=target[5:].split("|")[0])))
                    else:
                        site.proxy.append(Route(args[0], parse_target(target, {})))
                elif key == "sethandler" and args and args[0].startswith("proxy:"):
                    raw = args[0][len("proxy:"):]
                    if raw.startswith("unix:"):
                        site.fastcgi.append(Route("/", Target(raw, "unix", socket=raw[5:].split("|")[0])))
                    else:
                        site.fastcgi.append(Route("/", parse_target(raw.replace("fcgi://", "http://"), {}, 9000)))
                elif key in ("redirect", "redirectpermanent") and args:
                    url = args[-1]
                    if url.startswith("https://") and (len(args) == 1 or args[-2] == "/"):
                        site.https_redirect = True
                        site.redirect_to = url
                elif key in ("errorlog", "customlog") and args and args[0].startswith("/"):
                    site.log_paths.append(args[0])
            site.names = list(dict.fromkeys(names))
            sites.append(site)
    return _merge(sites)


def collect_apache(ctx: ScanContext) -> list[NginxSite]:
    main = next((p for p in APACHE_MAIN if ctx.host.is_file(p)), None)
    if main is None:
        return []
    root = str(PurePosixPath(main).parent)
    if root.endswith("/conf"):  # httpd.conf in /etc/httpd/conf: paths are relative to /etc/httpd
        root = str(PurePosixPath(root).parent)
    sites = parse_apache(_apache_files(ctx, main, root, 0, set()))
    for site in sites:
        if site.ssl_certificate:
            site.ssl_expires = read_cert_expiry(ctx, site.ssl_certificate)
    return sites


# -- Caddy ----------------------------------------------------------------------------------------------


def _caddy_blocks(text: str) -> list[tuple[str, str]]:
    """(address line, body) of each top-level block of a Caddyfile (global options and snippets included)."""
    out: list[tuple[str, str]] = []
    depth, head, body = 0, "", []
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        if depth == 0:
            if "{" not in line:
                continue  # an address without a block, or a lone directive: not a site we can read
            head, body = line[: line.index("{")].strip(), []
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                out.append((head, ""))
                depth = 0
            continue
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            out.append((head, "\n".join(body)))
            depth = 0
        else:
            body.append(line)
    return out


def _caddy_files(ctx: ScanContext, path: str, depth: int, seen: set[str]) -> list[tuple[str, str]]:
    if depth > MAX_DEPTH or path in seen or len(seen) > MAX_FILES:
        return []
    seen.add(path)
    text = ctx.host.read_text(path)
    if text is None:
        return []
    out = [(path, text)]
    for m in re.finditer(r"(?m)^\s*import\s+(\S+)", text):
        pattern = m.group(1)
        if not pattern.startswith("/"):
            pattern = f"{PurePosixPath(path).parent}/{pattern}"
        for f in sorted(ctx.host.glob(pattern)):
            out += _caddy_files(ctx, f, depth + 1, seen)
    return out


def _caddy_upstream(raw: str) -> Target:
    if raw.startswith("unix/"):
        return Target(raw, "unix", socket=raw[len("unix/"):])
    if "://" not in raw:
        raw_url = f"http://{raw}"
    else:
        raw_url = raw
    if raw.startswith(":"):
        raw_url = f"http://localhost{raw}"
    target = parse_target(raw_url, {})
    target.raw = raw
    return target


def parse_caddy(files: list[tuple[str, str]]) -> list[NginxSite]:
    sites: list[NginxSite] = []
    for path, text in files:
        for head, body in _caddy_blocks(text):
            if not head or head.startswith("(") or head.startswith("import"):
                continue  # global options, snippets
            names, ports, ssl = [], [], False
            for address in re.split(r"[\s,]+", head):
                if not address:
                    continue
                scheme = "http" if address.startswith("http://") else "https" if address.startswith("https://") else None
                bare = re.sub(r"^https?://", "", address)
                host, _, port = bare.partition(":")
                port_n = int(port) if port.isdigit() else (80 if scheme == "http" else 443)
                ports.append(port_n)
                if host and host not in ("localhost",) and not host.replace(".", "").isdigit():
                    names.append(host.lower())
                    if scheme != "http":
                        ssl = True  # automatic HTTPS
            site = NginxSite(names=list(dict.fromkeys(n for n in names if "*" not in n)), files=[path],
                             ports=sorted(set(ports)) or [443], ssl=ssl, https_redirect=ssl, server="caddy")
            for line in body.splitlines():
                words = line.split()
                if not words:
                    continue
                key, args = words[0], words[1:]
                if key == "reverse_proxy" and args:
                    if args[0].startswith("/") or args[0] == "*":
                        matcher, upstreams = args[0].rstrip("*") or "/", args[1:]
                    else:
                        matcher, upstreams = "/", args
                    upstreams = [u for u in upstreams if u != "{"]
                    if upstreams:
                        site.proxy.append(Route(matcher, _caddy_upstream(upstreams[0])))
                elif key == "root" and args:
                    site.root = args[-1].rstrip("/") or "/"
                elif key == "php_fastcgi" and args:
                    site.fastcgi.append(Route("/", _caddy_upstream(args[-1])))
                elif key in ("redir",) and args and args[-1].startswith("https://"):
                    site.redirect_to = args[-1]
                elif key == "output" and len(args) >= 2 and args[0] == "file":
                    site.log_paths.append(args[1])
            sites.append(site)
    return _merge(sites)


def _caddy_cert(ctx: ScanContext, name: str) -> str | None:
    for base in CADDY_CERTS:
        for path in ctx.host.glob(f"{base}/*/{name}/{name}.crt"):
            return path
    return None


def collect_caddy(ctx: ScanContext) -> list[NginxSite]:
    if not ctx.host.is_file(CADDYFILE):
        return []
    sites = parse_caddy(_caddy_files(ctx, CADDYFILE, 0, set()))
    for site in sites:
        cert = _caddy_cert(ctx, site.primary_name) if site.ssl and site.primary_name else None
        if cert:
            site.ssl_certificate = cert
            site.ssl_expires = read_cert_expiry(ctx, cert)
    return sites
