"""nginx vhost collector.

Parses ``sites-enabled/*`` and ``conf.d/*.conf`` (resolving ``include``), turns
every ``server`` block into a ``ServerBlock`` and merges the blocks that serve
the same domain (typically the port 80 -> 443 redirect written by certbot and
the real HTTPS block) into one ``NginxSite``.

Read-only: config files and certificates are only read.
"""

from __future__ import annotations

import glob as _glob
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from cryptography import x509

from mas.collectors import ScanContext

NGINX_DIR = "/etc/nginx"
SITE_GLOBS = [f"{NGINX_DIR}/sites-enabled/*", f"{NGINX_DIR}/conf.d/*.conf"]
MAX_INCLUDE_DEPTH = 8
REDIRECT_CODES = {"301", "302", "303", "307", "308"}
LOCAL_HOSTS = {"localhost", "0.0.0.0", "::1", "::", "*"}


class NginxParseError(ValueError):
    pass


# -- parsing ---------------------------------------------------------------


@dataclass
class Directive:
    name: str
    args: list[str]
    file: str
    line: int
    block: list[Directive] | None = None


def tokenize(text: str) -> Iterator[tuple[str, int, bool]]:
    """Yield ``(token, line, quoted)``. Comments are dropped."""
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            yield c, line, False
            i += 1
        elif c in "\"'":
            quote, start_line = c, line
            i += 1
            buf: list[str] = []
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n and text[i + 1] in (quote, "\\"):
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                buf.append(text[i])
                i += 1
            i += 1  # closing quote
            yield "".join(buf), start_line, True
        else:
            buf = []
            while i < n:
                c = text[i]
                if c.isspace() or c in ";{}":
                    break
                if c == "$" and i + 1 < n and text[i + 1] == "{":
                    end = text.find("}", i)
                    end = n - 1 if end == -1 else end
                    buf.append(text[i : end + 1])
                    i = end + 1
                    continue
                if c == "\\" and i + 1 < n:
                    buf.append(text[i : i + 2])
                    i += 2
                    continue
                buf.append(c)
                i += 1
            yield "".join(buf), line, False


def parse(text: str, file: str = "<string>") -> list[Directive]:
    tokens = list(tokenize(text))
    pos = 0

    def parse_block(depth: int) -> list[Directive]:
        nonlocal pos
        out: list[Directive] = []
        words: list[str] = []
        first_line = 0
        while pos < len(tokens):
            tok, line, quoted = tokens[pos]
            pos += 1
            if not quoted and tok == ";":
                if words:
                    out.append(Directive(words[0], words[1:], file, first_line))
                    words = []
            elif not quoted and tok == "{":
                if not words:
                    raise NginxParseError(f"{file}:{line}: unexpected '{{'")
                directive = Directive(words[0], words[1:], file, first_line)
                words = []
                directive.block = parse_block(depth + 1)
                out.append(directive)
            elif not quoted and tok == "}":
                if depth == 0:
                    raise NginxParseError(f"{file}:{line}: unexpected '}}'")
                if words:
                    raise NginxParseError(f"{file}:{line}: missing ';' before '}}'")
                return out
            else:
                if not words:
                    first_line = line
                words.append(tok)
        if depth > 0:
            raise NginxParseError(f"{file}: unexpected end of file, missing '}}'")
        if words:
            raise NginxParseError(f"{file}:{first_line}: missing ';'")
        return out

    return parse_block(0)


def load(ctx: ScanContext, path: str, depth: int = 0, stack: tuple[str, ...] = ()) -> list[Directive]:
    """Parse a file and inline its ``include`` directives."""
    text = ctx.host.read_text(path)
    if text is None:
        ctx.warn(f"nginx: cannot read {path}")
        return []
    try:
        directives = parse(text, path)
    except NginxParseError as exc:
        ctx.warn(f"nginx: cannot parse {exc}")
        return []
    return _expand_includes(ctx, directives, depth, stack + (path,))


def _expand_includes(
    ctx: ScanContext, directives: list[Directive], depth: int, stack: tuple[str, ...]
) -> list[Directive]:
    out: list[Directive] = []
    for d in directives:
        if d.name == "include" and d.args:
            pattern = d.args[0]
            if not pattern.startswith("/"):
                pattern = f"{NGINX_DIR}/{pattern}"
            if depth >= MAX_INCLUDE_DEPTH:
                ctx.warn(f"nginx: {d.file}:{d.line}: include depth limit reached")
                continue
            if _glob.has_magic(pattern):
                matches = ctx.host.glob(pattern)
            elif ctx.host.exists(pattern):
                matches = [pattern]
            else:
                ctx.warn(f"nginx: {d.file}:{d.line}: include {d.args[0]} not found")
                matches = []
            for match in matches:
                if match in stack:
                    ctx.warn(f"nginx: include loop on {match}")
                elif ctx.host.is_file(match):
                    out.extend(load(ctx, match, depth + 1, stack))
            continue
        if d.block is not None:
            d.block = _expand_includes(ctx, d.block, depth, stack)
        out.append(d)
    return out


# -- raw records -----------------------------------------------------------


@dataclass
class Target:
    """Where nginx sends a request (proxy_pass / fastcgi_pass)."""

    raw: str
    kind: str  # "tcp" | "unix" | "dynamic"
    host: str | None = None
    port: int | None = None
    socket: str | None = None
    upstream: str | None = None  # name of the upstream block, if any

    @property
    def is_local(self) -> bool:
        if self.kind == "unix":
            return True
        return self.kind == "tcp" and self.host is not None and (
            self.host in LOCAL_HOSTS or self.host.startswith("127.")
        )


@dataclass
class Route:
    location: str
    target: Target


@dataclass
class Listen:
    port: int | None
    address: str | None = None
    ssl: bool = False
    default_server: bool = False
    unix: str | None = None


@dataclass
class Location:
    uri: str
    proxy_pass: str | None = None
    fastcgi_pass: str | None = None
    root: str | None = None


@dataclass
class ServerBlock:
    file: str
    line: int
    names: list[str] = field(default_factory=list)
    listens: list[Listen] = field(default_factory=list)
    root: str | None = None
    ssl_on: bool = False
    ssl_certificate: str | None = None
    locations: list[Location] = field(default_factory=list)
    redirect_url: str | None = None  # https redirect (return / rewrite / certbot `if`)

    @property
    def domains(self) -> list[str]:
        return [n for n in self.names if n and n != "_" and not n.startswith("~")]

    @property
    def ssl(self) -> bool:
        return self.ssl_on or any(l.ssl for l in self.listens)

    @property
    def has_content(self) -> bool:
        return bool(self.root) or any(
            loc.proxy_pass or loc.fastcgi_pass or loc.root for loc in self.locations
        )

    @property
    def is_redirect_only(self) -> bool:
        return self.redirect_url is not None and not self.has_content


@dataclass
class NginxSite:
    names: list[str]
    files: list[str]
    ports: list[int]
    ssl: bool = False
    https_redirect: bool = False
    default_server: bool = False
    root: str | None = None
    proxy: list[Route] = field(default_factory=list)
    fastcgi: list[Route] = field(default_factory=list)
    ssl_certificate: str | None = None
    ssl_expires: datetime | None = None
    redirect_to: str | None = None

    @property
    def primary_name(self) -> str | None:
        return self.names[0] if self.names else None


# -- interpretation --------------------------------------------------------


def _split_hostport(hostport: str, default_port: int | None) -> tuple[str, int | None]:
    if hostport.startswith("["):
        host, _, rest = hostport[1:].partition("]")
        port = rest.lstrip(":")
    elif hostport.count(":") == 1:
        host, port = hostport.split(":")
    else:
        host, port = hostport, ""
    return host, int(port) if port.isdigit() else default_port


def parse_target(raw: str, upstreams: dict[str, list[str]], default_port: int | None = 80) -> Target:
    if "$" in raw:
        return Target(raw, "dynamic")
    rest, scheme = raw, None
    m = re.match(r"^([a-z0-9+]+)://", rest)
    if m:
        scheme, rest = m.group(1), rest[m.end():]
    if rest.startswith("unix:"):
        # proxy_pass http://unix:/run/app.sock:/prefix
        return Target(raw, "unix", socket=rest[len("unix:"):].split(":", 1)[0])
    hostport = rest.split("/", 1)[0]
    if upstreams.get(hostport):
        inner = parse_target(upstreams[hostport][0], {}, default_port)
        inner.raw, inner.upstream = raw, hostport
        return inner
    port = 443 if scheme in ("https", "grpcs") else default_port
    host, port = _split_hostport(hostport, port)
    return Target(raw, "tcp", host=host, port=port)


def _parse_listen(args: list[str]) -> Listen:
    first, flags = args[0], set(args[1:])
    ssl = "ssl" in flags or "quic" in flags
    default = "default_server" in flags or "default" in flags
    if first.startswith("unix:"):
        return Listen(None, None, ssl, default, unix=first[len("unix:"):])
    if first.isdigit():
        addr, port = None, int(first)
    else:
        addr, port = _split_hostport(first, 80)
    return Listen(port, addr, ssl or port == 443, default)


def _https_redirect(args: list[str]) -> str | None:
    """Return the URL if ``return`` args redirect to https."""
    if len(args) == 1 and args[0].startswith("https://"):
        return args[0]
    if len(args) >= 2 and args[0] in REDIRECT_CODES and args[1].startswith("https://"):
        return args[1]
    return None


def _parse_locations(d: Directive) -> list[Location]:
    loc = Location(uri=d.args[-1] if d.args else "")
    out = [loc]
    for c in d.block or []:
        if c.name == "proxy_pass" and c.args:
            loc.proxy_pass = loc.proxy_pass or c.args[0]
        elif c.name == "fastcgi_pass" and c.args:
            loc.fastcgi_pass = loc.fastcgi_pass or c.args[0]
        elif c.name in ("root", "alias") and c.args:
            loc.root = loc.root or c.args[0]
        elif c.name == "location":
            out.extend(_parse_locations(c))
        elif c.name == "if":
            for cc in c.block or []:
                if cc.name == "proxy_pass" and cc.args:
                    loc.proxy_pass = loc.proxy_pass or cc.args[0]
                elif cc.name == "fastcgi_pass" and cc.args:
                    loc.fastcgi_pass = loc.fastcgi_pass or cc.args[0]
    return out


def server_from_directive(d: Directive) -> ServerBlock:
    sb = ServerBlock(file=d.file, line=d.line)
    for c in d.block or []:
        if c.name == "listen" and c.args:
            sb.listens.append(_parse_listen(c.args))
        elif c.name == "server_name":
            sb.names.extend(c.args)
        elif c.name == "root" and c.args:
            sb.root = c.args[0]
        elif c.name == "ssl" and c.args == ["on"]:
            sb.ssl_on = True
        elif c.name == "ssl_certificate" and c.args and not sb.ssl_certificate:
            sb.ssl_certificate = c.args[0]
        elif c.name == "return":
            sb.redirect_url = sb.redirect_url or _https_redirect(c.args)
        elif c.name == "rewrite" and len(c.args) >= 2 and c.args[1].startswith("https://"):
            if len(c.args) < 3 or c.args[2] in ("permanent", "redirect"):
                sb.redirect_url = sb.redirect_url or c.args[1]
        elif c.name == "if":
            # certbot: if ($host = example.com) { return 301 https://$host$request_uri; }
            for cc in c.block or []:
                if cc.name == "return":
                    sb.redirect_url = sb.redirect_url or _https_redirect(cc.args)
        elif c.name == "location":
            sb.locations.extend(_parse_locations(c))
    if not sb.listens:
        sb.listens.append(Listen(80))
    return sb


def _walk_servers(directives: list[Directive]) -> Iterator[Directive]:
    for d in directives:
        if d.name in ("stream", "upstream", "mail"):
            continue
        if d.name == "server" and d.block is not None:
            yield d
        elif d.block:
            yield from _walk_servers(d.block)


def _walk_upstreams(directives: list[Directive], out: dict[str, list[str]]) -> None:
    for d in directives:
        if d.name == "upstream" and d.args and d.block is not None:
            out[d.args[0]] = [c.args[0] for c in d.block if c.name == "server" and c.args]
        elif d.block and d.name != "stream":
            _walk_upstreams(d.block, out)


def _redirect_host(url: str) -> str | None:
    host = re.split(r"[/$?]", url[len("https://"):], maxsplit=1)[0]
    if not host:  # https://$host$request_uri: same names
        return None
    return _split_hostport(host, None)[0]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _build_site(blocks: list[ServerBlock], upstreams: dict[str, list[str]]) -> NginxSite:
    blocks = sorted(blocks, key=lambda b: not b.ssl)  # HTTPS blocks are authoritative
    site = NginxSite(
        names=_dedupe([n for b in blocks for n in b.domains]),
        files=_dedupe([b.file for b in blocks]),
        ports=sorted({l.port for b in blocks for l in b.listens if l.port}),
        ssl=any(b.ssl for b in blocks),
        https_redirect=any(b.redirect_url for b in blocks),
        default_server=any(l.default_server for b in blocks for l in b.listens),
    )
    site.root = next((b.root for b in blocks if b.root), None) or next(
        (loc.root for b in blocks for loc in b.locations if loc.root), None
    )
    site.ssl_certificate = next((b.ssl_certificate for b in blocks if b.ssl_certificate), None)
    seen: set[tuple[str, str, str]] = set()
    for b in blocks:
        for loc in b.locations:
            for kind, raw in (("proxy", loc.proxy_pass), ("fastcgi", loc.fastcgi_pass)):
                if not raw or (kind, loc.uri, raw) in seen:
                    continue
                seen.add((kind, loc.uri, raw))
                default_port = 80 if kind == "proxy" else 9000
                route = Route(loc.uri, parse_target(raw, upstreams, default_port))
                (site.proxy if kind == "proxy" else site.fastcgi).append(route)
    return site


def merge_blocks(blocks: list[ServerBlock], upstreams: dict[str, list[str]]) -> list[NginxSite]:
    content = [b for b in blocks if b.has_content]
    redirects = [b for b in blocks if b.is_redirect_only]

    # Group content blocks sharing at least one domain.
    groups: list[list[ServerBlock]] = []
    for b in content:
        names = set(b.domains)
        touching = [g for g in groups if names & {n for x in g for n in x.domains}]
        merged = [b] + [x for g in touching for x in g]
        groups = [g for g in groups if all(g is not t for t in touching)] + [merged]

    sites = [_build_site(g, upstreams) for g in groups]

    for r in redirects:
        host = _redirect_host(r.redirect_url or "")
        targets = [host] if host else r.domains
        site = next((s for s in sites if set(targets) & set(s.names)), None)
        if site is not None:
            site.names = _dedupe(site.names + r.domains)
            site.files = _dedupe(site.files + [r.file])
            site.ports = sorted(set(site.ports) | {l.port for l in r.listens if l.port})
            site.https_redirect = True
        elif r.domains:
            sites.append(
                NginxSite(
                    names=r.domains,
                    files=[r.file],
                    ports=sorted({l.port for l in r.listens if l.port}),
                    redirect_to=r.redirect_url,
                )
            )
    return sorted(sites, key=lambda s: (not s.names, s.primary_name or "", s.files[0]))


def read_cert_expiry(ctx: ScanContext, path: str) -> datetime | None:
    if "$" in path:
        return None
    data = ctx.host.read_bytes(path)
    if not data:
        ctx.warn(f"nginx: cannot read certificate {path}")
        return None
    try:
        cert = x509.load_pem_x509_certificate(data)
    except ValueError:
        try:
            cert = x509.load_der_x509_certificate(data)
        except ValueError:
            ctx.warn(f"nginx: invalid certificate {path}")
            return None
    return cert.not_valid_after_utc


def collect(ctx: ScanContext) -> list[NginxSite]:
    host = ctx.host
    if not host.is_dir(NGINX_DIR):
        ctx.warn(f"nginx: {NGINX_DIR} not found, no vhost detected")
        return []

    files = [
        f
        for pattern in SITE_GLOBS
        for f in host.glob(pattern)
        if not PurePosixPath(f).name.startswith(".") and host.is_file(f)
    ]
    directives = [d for f in files for d in load(ctx, f)]

    upstreams: dict[str, list[str]] = {}
    main_conf = host.read_text(f"{NGINX_DIR}/nginx.conf")
    if main_conf:
        try:  # only for upstream blocks; its includes are the files above
            _walk_upstreams(parse(main_conf, f"{NGINX_DIR}/nginx.conf"), upstreams)
        except NginxParseError as exc:
            ctx.warn(f"nginx: cannot parse {exc}")
    _walk_upstreams(directives, upstreams)

    blocks = [server_from_directive(d) for d in _walk_servers(directives)]
    sites = merge_blocks(blocks, upstreams)
    for site in sites:
        if site.ssl_certificate:
            site.ssl_expires = read_cert_expiry(ctx, site.ssl_certificate)
    return sites
