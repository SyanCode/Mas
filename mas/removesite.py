"""Remove a site nginx serves but nothing runs behind any more (a vhost left after a container or an app
was stopped for good).

Only files that serve this site and nothing else are touched, never a file Mas writes for its own apps:
a link in sites-enabled is removed (its target in sites-available stays), any other file is moved to
``/etc/nginx/mas-removed/`` (outside every include). Then ``nginx -t``: if it fails everything is put
back, otherwise nginx reloads. The certificate can be deleted too, when no other site uses it.
"""

from __future__ import annotations

import re
import time
from pathlib import PurePosixPath

from mas.host import Host
from mas.models import Service, Status

REMOVED_DIR = "/etc/nginx/mas-removed"
OWN_FILES = re.compile(r"^(mas|mas-app-.*|mas-supabase.*|mas-maps|mas-maintenance)\.conf$")
SERVER_NAME_RE = re.compile(r"^\s*server_name\s+([^;]+);", re.M)
CERT_RE = re.compile(r"/etc/letsencrypt/live/([A-Za-z0-9.-]+)/")


class RemoveError(Exception):
    pass


def _read(host: Host, path: str) -> str:
    if host.is_symlink(path):  # sites-enabled links (tests keep the target as the file content)
        target = host.readlink(path) or path
        path = target if target.startswith("/") else str(PurePosixPath(path).parent / target)
    return host.read_text(path) or ""


def _names(text: str) -> set[str]:
    out: set[str] = set()
    for value in SERVER_NAME_RE.findall(re.sub(r"#[^\n]*", "", text)):
        out.update(n.lower() for n in value.split() if n not in ("_", '""'))
    return out


def check(host: Host, svc: Service) -> list[str]:
    """The nginx files to remove; raises when this site cannot be removed safely."""
    if not svc.nginx_files:
        raise RemoveError("this service has no nginx site")
    if svc.status == Status.RUNNING:
        raise RemoveError("something still answers behind this site: stop it first")
    wanted = {d.lower() for d in svc.domains}
    wanted |= {f"www.{d}" for d in wanted} | {d[4:] for d in wanted if d.startswith("www.")}
    for path in svc.nginx_files:
        name = PurePosixPath(path).name
        if OWN_FILES.match(name):
            raise RemoveError(f"{path} belongs to Mas: remove the app instead")
        if not path.startswith(("/etc/nginx/sites-enabled/", "/etc/nginx/conf.d/")):
            raise RemoveError(f"{path} is included from another file: remove it by hand")
        others = _names(_read(host, path)) - wanted
        if others:
            raise RemoveError(f"{path} also serves {', '.join(sorted(others))}")
    return list(svc.nginx_files)


def certificates(host: Host, files: list[str]) -> list[str]:
    """Certificates of these files that no other enabled site uses."""
    names = {m for f in files for m in CERT_RE.findall(_read(host, f))}
    others = [p for p in host.glob("/etc/nginx/sites-enabled/*") + host.glob("/etc/nginx/conf.d/*.conf") if p not in files]
    used = {m for p in others for m in CERT_RE.findall(_read(host, p))}
    return sorted(names - used)


def remove(host: Host, svc: Service, delete_certificate: bool = False) -> list[str]:
    files = check(host, svc)
    certs = certificates(host, files) if delete_certificate else []
    stamp = time.strftime("%Y%m%d%H%M%S")
    undo: list[tuple[str, str | None, str | None]] = []  # (path, link target or None, moved to)
    done = []
    for path in files:
        if host.is_symlink(path):
            target = host.readlink(path)
            host.remove(path)
            undo.append((path, target, None))
            done.append(f"disabled {path} (its file stays in {target})")
        else:
            dest = f"{REMOVED_DIR}/{PurePosixPath(path).name}.{stamp}"
            host.makedirs(REMOVED_DIR)
            host.rename(path, dest)
            undo.append((path, None, dest))
            done.append(f"moved {path} to {dest}")
    test = host.run(["nginx", "-t"], timeout=60)
    if test is None or not test.ok:
        for path, target, moved in undo:
            if target:
                host.symlink_atomic(target, path)
            elif moved:
                host.rename(moved, path)
        detail = (test.stderr if test else "nginx not found").strip()
        raise RemoveError(f"nginx -t failed without this site, nothing changed:\n{detail}")
    host.run(["systemctl", "reload", "nginx"], timeout=60)
    done.append("nginx reloaded")
    for name in certs:
        res = host.run(["certbot", "delete", "--non-interactive", "--cert-name", name], timeout=120)
        done.append(f"certificate {name} deleted" if res is not None and res.ok
                    else f"certificate {name} kept: certbot delete failed")
    return done
