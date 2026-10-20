"""Mas configuration (``/etc/mas/config.toml``)."""

from __future__ import annotations

import fnmatch
import logging
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
    return cfg
