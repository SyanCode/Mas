"""Server health checks: certificates, certbot renewal, nginx, DNS. Read-only.

Each check explains the problem and, when there is a safe fix, the command
to run. Nothing here changes the system: a check that can be fixed from the
dashboard names its fix (``action``, ``target``), which mas.healthfix applies.
"""

from __future__ import annotations

import socket
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from cryptography import x509

from mas.collectors import ScanContext
from mas.collectors import nginx as nginx_collector
from mas.collectors import ports as ports_collector
from mas.collectors.nginx import NginxSite
from mas.collectors.ports import ListeningSocket
from mas.config import Config
from mas.host import Host

RENEWAL_DIR = "/etc/letsencrypt/renewal"
LIVE_DIR = "/etc/letsencrypt/live"
SOON_DAYS = 14


@dataclass
class Check:
    level: str  # error | warning | info
    category: str  # certificates | nginx | dns
    title: str
    detail: str = ""
    fix: str | None = None
    action: str | None = None  # one-click fix in mas.healthfix
    target: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Lineage:
    name: str
    params: dict[str, str]
    domains: list[str]
    expires: datetime | None


def parse_renewal(text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith(("#", "[")):
            params.setdefault(key.strip(), value.strip().rstrip(","))
    return params


def _read_cert(host: Host, path: str) -> tuple[datetime | None, list[str]]:
    data = host.read_bytes(path)
    if not data:
        return None, []
    try:
        cert = x509.load_pem_x509_certificate(data)
    except ValueError:
        return None, []
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.DNSName)
    except x509.ExtensionNotFound:
        names = [a.value for a in cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)]
    return cert.not_valid_after_utc, [str(n) for n in names]


def load_lineages(host: Host) -> list[Lineage]:
    out = []
    for conf in host.glob(f"{RENEWAL_DIR}/*.conf"):
        name = PurePosixPath(conf).name.removesuffix(".conf")
        params = parse_renewal(host.read_text(conf) or "")
        expires, domains = _read_cert(host, params.get("fullchain") or f"{LIVE_DIR}/{name}/fullchain.pem")
        out.append(Lineage(name, params, domains, expires))
    return out


def check_certificates(host: Host, sites: list[NginxSite], lineages: list[Lineage],
                       now: datetime | None = None) -> list[Check]:
    now = now or datetime.now(timezone.utc)
    checks: list[Check] = []
    served = {n for s in sites for n in s.names}
    used_paths = {s.ssl_certificate for s in sites if s.ssl_certificate}

    for lin in lineages:
        live = f"{LIVE_DIR}/{lin.name}"
        cert_path = lin.params.get("cert") or f"{live}/cert.pem"
        retire = (f"sudo mkdir -p /root/letsencrypt-retired && sudo mv {RENEWAL_DIR}/{lin.name}.conf "
                  f"/root/letsencrypt-retired/")
        if not host.exists(cert_path):
            checks.append(Check("error", "certificates", f"{lin.name}: renewal config without certificate",
                                f"{RENEWAL_DIR}/{lin.name}.conf points to {cert_path}, which does not exist. "
                                "certbot fails on it at every run.", retire, "cert-retire", lin.name))
            continue
        used = (lin.params.get("fullchain") or f"{live}/fullchain.pem") in used_paths or any(
            d in served for d in lin.domains)
        if not host.is_symlink(cert_path):
            checks.append(Check("error", "certificates", f"{lin.name}: broken certbot lineage",
                                f"{cert_path} should be a symlink to ../../archive; certbot skips this "
                                "certificate, it will never be renewed (usually a copy without `cp -a`).",
                                f"If unused: {retire}. If used: move it away and request a new one with "
                                f"`certbot certonly --nginx -d {lin.name}`.",
                                None if used else "cert-retire", None if used else lin.name))

        if lin.expires is not None:
            days = (lin.expires - now).days
            if days < 0:
                checks.append(Check("error" if used else "warning", "certificates", f"{lin.name}: expired",
                                    f"Expired on {lin.expires:%Y-%m-%d}." + ("" if used else " Not used by nginx.")))
            elif days < SOON_DAYS:
                checks.append(Check("warning", "certificates", f"{lin.name}: expires in {days} days",
                                    "certbot renews 30 days before expiry: the renewal is failing.",
                                    f"sudo certbot renew --cert-name {lin.name} --dry-run",
                                    "cert-renew-test", lin.name))
        if not used:
            checks.append(Check(
                "info", "certificates", f"{lin.name}: not used by any enabled site",
                f"No nginx vhost loaded from nginx.conf serves {', '.join(lin.domains) or lin.name}; certbot still "
                "tries to renew it. Make sure no other program (Docker, another web server) uses it before removing.",
                f"sudo grep -R '{lin.name}' /etc/nginx /etc/docker /opt 2>/dev/null   # then, if really unused: "
                f"sudo certbot delete --cert-name {lin.name}",
                "cert-delete", lin.name,
            ))

        auth = lin.params.get("authenticator", "")
        hooks = " ".join(lin.params.get(k, "") for k in ("pre_hook", "post_hook"))
        if auth == "standalone" and "nginx" in hooks:
            checks.append(Check(
                "warning", "certificates", f"{lin.name}: renewal stops nginx",
                "The standalone authenticator stops nginx during the renewal: every site is down for a moment, "
                "and stays down if nginx fails to start again.",
                f"sudo sed -i -e 's/^authenticator = standalone$/authenticator = nginx/' -e '/^pre_hook = /d' "
                f"-e '/^post_hook = /d' {RENEWAL_DIR}/{lin.name}.conf",
                "renewal-nginx", lin.name,
            ))
        if auth == "webroot":
            for path in (lin.params.get("webroot_path") or "").split(","):
                if path.strip() and not host.is_dir(path.strip()):
                    checks.append(Check("warning", "certificates", f"{lin.name}: webroot missing",
                                        f"{path.strip()} does not exist; the HTTP challenge will fail."))

    if lineages:
        res = host.run(["systemctl", "is-active", "certbot.timer"])
        if res is not None and res.stdout.strip() not in ("active", ""):
            checks.append(Check("warning", "certificates", "certbot.timer is not active",
                                "Certificates are not renewed automatically.",
                                "sudo systemctl enable --now certbot.timer", "certbot-timer", "certbot.timer"))
    return checks


def check_nginx(host: Host, sites: list[NginxSite], sockets: list[ListeningSocket]) -> list[Check]:
    checks: list[Check] = []
    for site in sites:
        if site.ssl_certificate and "$" not in site.ssl_certificate and not host.exists(site.ssl_certificate):
            checks.append(Check("error", "nginx", f"{site.primary_name or site.files[0]}: certificate file missing",
                                f"{site.ssl_certificate} does not exist ({', '.join(site.files)}). "
                                "nginx will refuse to start or reload."))

    nginx_pids = {pid for s in sockets if s.process == "nginx" for pid in s.pids}
    if nginx_pids:
        res = host.run(["systemctl", "show", "nginx", "-p", "MainPID", "--value"])
        main = res.stdout.strip() if res is not None and res.ok else ""
        if main.isdigit() and int(main) not in nginx_pids:
            checks.append(Check(
                "error", "nginx", "nginx runs outside systemd",
                f"The nginx listening on 80/443 (pid {min(nginx_pids)}) is not the one systemd manages "
                f"(MainPID={main}). `systemctl reload nginx` has no effect and a restart will fail.",
                "sudo pkill -9 -x nginx; sleep 1; sudo systemctl start nginx   # sites down ~1 s",
                "nginx-systemd", "nginx",
            ))

    test = host.run(["nginx", "-t"])
    if test is not None and not test.ok:
        checks.append(Check("error", "nginx", "nginx configuration test fails",
                            (test.stderr or test.stdout).strip()[-1500:]))
    return checks


def _resolve(name: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)})
    except OSError:
        return []


def resolve_all(names: set[str], resolver=_resolve) -> dict[str, list[str]]:
    names = {n for n in names if "." in n and not n.startswith(("*", "~"))}
    results: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(resolver, n): n for n in sorted(names)}
        done, _ = wait(futures, timeout=15)
        for fut in done:
            results[futures[fut]] = fut.result()
    return results


def majority_ip(results: dict[str, list[str]]) -> str | None:
    """The address most served domains resolve to (at least two): this server."""
    counts = Counter(ip for ips in results.values() for ip in ips)
    if counts and counts.most_common(1)[0][1] >= 2:
        return counts.most_common(1)[0][0]
    return None


def guess_public_ip(host: Host, resolver=_resolve) -> str | None:
    sites = nginx_collector.collect(ScanContext(host))
    return majority_ip(resolve_all({n for s in sites for n in s.names}, resolver))


def check_dns(names: set[str], public_ip: str | None, resolver=_resolve) -> list[Check]:
    results = resolve_all(names, resolver)
    server_ip = public_ip or majority_ip(results)
    checks = []
    for name, ips in sorted(results.items()):
        if not ips:
            checks.append(Check("warning", "dns", f"{name} does not resolve",
                                "No DNS record: the site is unreachable and its certificate cannot be renewed."))
        elif server_ip and server_ip not in ips:
            checks.append(Check("warning", "dns", f"{name} points elsewhere",
                                f"{name} resolves to {', '.join(ips)}, not to this server ({server_ip}). "
                                "Its vhost/certificate here are probably leftovers.",
                                "Update the DNS record, or remove the vhost and `certbot delete` its certificate."))
    return checks


def run_checks(host: Host, config: Config | None = None, dns: bool = True, public_ip: str | None = None) -> list[Check]:
    ctx = ScanContext(host, config or Config())
    sites = nginx_collector.collect(ctx)
    sockets = ports_collector.collect(ctx)
    lineages = load_lineages(host)
    checks = check_nginx(host, sites, sockets) + check_certificates(host, sites, lineages)
    if dns:
        names = {n for s in sites for n in s.names} | {d for lin in lineages for d in lin.domains}
        checks += check_dns(names, public_ip or ctx.config.public_ip)
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(checks, key=lambda c: (order[c.level], c.category, c.title))
