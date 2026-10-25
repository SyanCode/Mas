"""Mas configuration (``/etc/mas/config.toml``)."""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import tomllib
from dataclasses import dataclass, field

from mas.host import Host

log = logging.getLogger(__name__)

CONFIG_PATH = "/etc/mas/config.toml"

# systemd units that are part of the OS, not "sites". Glob patterns, matched
# against the unit name with and without the ".service" suffix.
DEFAULT_EXCLUDED_UNITS = [
    "ssh", "sshd", "cron", "anacron", "atd", "nginx", "dbus", "dbus-broker",
    "systemd-*", "getty@*", "serial-getty@*", "user@*", "polkit", "rsyslog",
    "unattended-upgrades", "networkd-dispatcher", "NetworkManager", "ModemManager",
    "wpa_supplicant", "accounts-daemon", "udisks2", "upower", "snapd", "containerd",
    "docker", "chrony", "chronyd", "ntp", "ntpsec", "qemu-guest-agent", "fail2ban",
    "php*-fpm", "mas", "irqbalance", "multipathd", "packagekit", "rpcbind",
    "exim4", "postfix", "haveged", "apparmor", "auditd", "cloud-*", "google-*",
    "ufw", "thermald", "lvm2-*", "open-vm-tools", "vgauth", "pm2-*", "certbot",
    "rtkit-daemon", "avahi-daemon", "cups", "cups-browsed", "bluetooth", "smartd",
    "ifup@*", "networking", "lxcfs", "iscsid", "open-iscsi", "nscd", "nslcd",
]

# Listening processes (``comm``) that never are user services.
DEFAULT_EXCLUDED_PROCESSES = [
    "nginx", "sshd", "systemd*", "chronyd", "exim4", "master", "dockerd",
    "containerd*", "rpcbind", "cupsd", "avahi-daemon", "php-fpm*", "dnsmasq",
]


@dataclass
class Config:
    excluded_units: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_UNITS))
    excluded_processes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PROCESSES))
    # Hide systemd units with no port, no working directory and a command that
    # only references system paths (colord, smartd, preload, ...).
    hide_os_daemons: bool = True

    # [server]
    port: int = 7070
    cookie_secure: bool = True
    session_hours: int = 12
    scan_interval: int = 60
    # [paths]
    db_path: str = "/var/lib/mas/mas.db"
    src_dir: str = "/opt/mas/src"
    venv_dir: str = "/opt/mas/venv"
    web_dir: str | None = None  # default: <src_dir>/web/out
    secret_key_path: str = "/etc/mas/secret.key"
    # [github] (phase 3)
    github_app_id: int | None = None
    github_app_slug: str | None = None
    github_private_key_path: str = "/etc/mas/github-app.pem"
    github_webhook_secret: str | None = None
    # [deploy]
    apps_dir: str = "/srv/mas/apps"
    pages_dir: str = "/srv/mas/pages"  # status pages served by nginx (deploying, unavailable)
    supabase_dir: str = "/srv/mas/supabase"
    supabase_keep_backups: int = 7
    stacks_keep_backups: int = 7  # backups kept per ready-made service
    keep_releases: int = 5
    letsencrypt_email: str | None = None
    port_min: int = 7100
    port_max: int = 7999
    healthcheck_timeout: int = 60
    dns_wait_seconds: int = 120  # wait for a new DNS record before certbot
    public_ip: str | None = None  # [health] override for DNS checks
    cluster_role: str = "primary"  # [cluster] "agent": no dashboard, driven by the primary server
    cluster_address: str | None = None  # [cluster] private address an agent listens on

    @property
    def github_configured(self) -> bool:
        return bool(self.github_app_id and self.github_webhook_secret)

    def unit_excluded(self, unit: str) -> bool:
        base = unit[: -len(".service")] if unit.endswith(".service") else unit
        return any(
            fnmatch.fnmatchcase(base, pat) or fnmatch.fnmatchcase(unit, pat)
            for pat in self.excluded_units
        )

    def process_excluded(self, comm: str | None) -> bool:
        return bool(comm) and any(fnmatch.fnmatchcase(comm, p) for p in self.excluded_processes)


def load_config(host: Host, path: str = CONFIG_PATH) -> Config:
    """Load the config file if present. A broken file falls back to defaults."""
    cfg = Config()
    text = host.read_text(path)
    if text is None:
        return cfg
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        log.warning("ignoring invalid %s: %s", path, exc)
        return cfg
    scan = data.get("scan", {})
    if isinstance(scan.get("exclude_units"), list):
        cfg.excluded_units = [str(x) for x in scan["exclude_units"]]
    if isinstance(scan.get("extra_exclude_units"), list):
        cfg.excluded_units += [str(x) for x in scan["extra_exclude_units"]]
    if isinstance(scan.get("exclude_processes"), list):
        cfg.excluded_processes = [str(x) for x in scan["exclude_processes"]]
    if isinstance(scan.get("hide_os_daemons"), bool):
        cfg.hide_os_daemons = scan["hide_os_daemons"]

    server = data.get("server", {})
    for key, typ in (("port", int), ("cookie_secure", bool), ("session_hours", int), ("scan_interval", int)):
        if isinstance(server.get(key), typ):
            setattr(cfg, key, server[key])
    paths = data.get("paths", {})
    for key, attr in (("db", "db_path"), ("src", "src_dir"), ("venv", "venv_dir"), ("web", "web_dir"),
                      ("secret_key", "secret_key_path")):
        if isinstance(paths.get(key), str):
            setattr(cfg, attr, paths[key])

    gh = data.get("github", {})
    if isinstance(gh.get("app_id"), int):
        cfg.github_app_id = gh["app_id"]
    for key, attr in (("app_slug", "github_app_slug"), ("private_key_path", "github_private_key_path"),
                      ("webhook_secret", "github_webhook_secret")):
        if isinstance(gh.get(key), str):
            setattr(cfg, attr, gh[key])

    deploy = data.get("deploy", {})
    for key, typ in (("apps_dir", str), ("keep_releases", int), ("letsencrypt_email", str),
                     ("port_min", int), ("port_max", int), ("healthcheck_timeout", int),
                     ("supabase_keep_backups", int), ("stacks_keep_backups", int)):
        if isinstance(deploy.get(key), typ):
            setattr(cfg, key, deploy[key])
    if isinstance(data.get("health", {}).get("public_ip"), str):
        cfg.public_ip = data["health"]["public_ip"]
    cluster = data.get("cluster", {})
    if cluster.get("role") in ("primary", "agent"):
        cfg.cluster_role = cluster["role"]
    if isinstance(cluster.get("address"), str):
        cfg.cluster_address = cluster["address"]
    return cfg


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))  # a JSON string is a valid TOML basic string


def save_section(host: Host, section: str, values: dict, path: str = CONFIG_PATH) -> None:
    """Replace one ``[section]`` of config.toml, keeping the rest (backup first)."""
    text = host.read_text(path) or ""
    out, skipping = [], False
    for line in text.splitlines():
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            skipping = header.group(1).strip() == section
        if not skipping:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    out += ["", f"[{section}]"] + [f"{k} = {_toml_value(v)}" for k, v in values.items() if v is not None]
    host.backup(path)
    host.write_text(path, "\n".join(out).lstrip("\n") + "\n", mode=0o600)
