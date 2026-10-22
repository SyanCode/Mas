"""Prepare a temporary replacement host for a maintenance window.

The replacement is a host file copy, unlike the app replicas managed by the
cluster. Mas, nginx and Docker can be stopped during the final sync. Other
service processes and external databases need separate handling. Network
ownership (DNS, public IP or router rules) is managed separately.
"""

from __future__ import annotations

import re

from mas.host import Host


class ReplacementError(Exception):
    pass


REMOTE_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+$")

# Full Mas state plus nginx and TLS material. Paths are explicit to avoid
# copying unrelated operating-system state or SSH credentials.
TRANSFER_PATHS = (
    "/etc/mas/",
    "/etc/nginx/",
    "/etc/letsencrypt/",
    "/var/lib/mas/",
    "/srv/mas/",
    "/opt/mas/src/",
    "/opt/mas/venv/",
)


class Replacement:
    def __init__(self, host: Host) -> None:
        self.host = host

    def prepare(self, remote: str, *, final: bool = False) -> dict:
        """Install prerequisites and copy the current host to ``user@host``.

        A live sync is useful as a first pass. ``final=True`` stops Mas, nginx
        and Docker for the last file sync, then restarts them. Other services
        and external databases need their own quiesce/backup procedure. Routing
        is never changed here.
        """
        remote = remote.strip()
        if not REMOTE_RE.fullmatch(remote):
            raise ReplacementError("use a remote SSH target in the form user@host")
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", remote]
        probe = self.host.run(ssh + ["true"], timeout=20)
        if probe is None or not probe.ok:
            raise ReplacementError("SSH connection failed; configure key-based root access to the replacement")

        # The installer is expected to exist on the replacement. Installing
        # from the source checkout also makes the operation independent of a
        # public package registry.
        install = self.host.run(ssh + ["bash", "-lc", "test -x /usr/local/bin/mas"], timeout=20)
        if install is None or not install.ok:
            raise ReplacementError("install Mas on the replacement first with deploy/install.sh")

        stopped: list[list[str]] = []
        try:
            if final:
                for unit in ("mas", "nginx", "docker"):
                    result = self.host.run(["systemctl", "stop", unit], timeout=120)
                    if result is not None and result.ok:
                        stopped.append(["systemctl", "start", unit])
            for path in TRANSFER_PATHS:
                result = self.host.run(["rsync", "-aHAX", "--numeric-ids", "--delete",
                                        "--rsync-path", "sudo rsync", "-e",
                                        "ssh -o BatchMode=yes", path, f"{remote}:{path}"], timeout=6 * 3600)
                if result is None or not result.ok:
                    detail = result.stderr.strip()[-400:] if result else "rsync is not installed"
                    raise ReplacementError(f"transfer of {path} failed: {detail}")
        finally:
            for command in reversed(stopped):
                self.host.run(command, timeout=120)

        return {"remote": remote, "final": final, "paths": list(TRANSFER_PATHS),
                "routing_changed": False,
                "next": "Switch the public IP, DNS records or router port forwards to the replacement, verify services, then disconnect the original."}
