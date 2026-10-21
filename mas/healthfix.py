"""One-click fixes for the health checks.

The dashboard never sends a command: it names a fix (``cert-delete``…) and its target. Mas runs the
checks again and applies the fix only when that very problem is still reported, with the code below.
What it removes is kept first: a certificate goes to an archive, a renewal file is moved or backed up.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from mas.config import Config
from mas.health import RENEWAL_DIR, SWAP_FILE, meminfo, run_checks, swap_size_mb
from mas.host import Host

# Shown on the button and in the confirmation (English keys, translated by the dashboard).
ACTIONS: dict[str, dict] = {
    "cert-delete": {"label": "Delete the certificate", "danger": True,
                    "confirm": "Mas checks that no configuration uses it, keeps a copy in "
                               "/var/lib/mas/backups/certbot, then runs certbot delete."},
    "cert-retire": {"label": "Retire the renewal file", "danger": False,
                    "confirm": "The renewal file is moved to /root/letsencrypt-retired: certbot stops failing on it."},
    "cert-renew-test": {"label": "Test the renewal", "danger": False,
                        "confirm": "Runs certbot renew --dry-run: nothing changes, the output tells why it fails."},
    "renewal-nginx": {"label": "Renew through nginx", "danger": False,
                      "confirm": "The renewal file is backed up, switched to the nginx method (no more nginx "
                                 "stop), then a test renewal checks it; it is restored if the test fails."},
    "certbot-timer": {"label": "Turn on automatic renewal", "danger": False,
                      "confirm": "Runs systemctl enable --now certbot.timer."},
    "nginx-systemd": {"label": "Restart nginx under systemd", "danger": True,
                      "confirm": "Every site is down for about a second while the stray nginx is stopped and "
                                 "systemd starts it again."},
    "swap-create": {"label": "Create a swap file", "danger": False,
                    "confirm": "Mas creates /swapfile (1 or 2 GB, if the disk keeps 2 GB free), turns it on now and "
                               "at boot (swapfile.swap) and sets vm.swappiness=10: it is only used when the memory "
                               "is full."},
}
# Run by systemd (PID 1) through systemd-run: mas.service cannot write /etc nor kernel settings.
SWAP_SCRIPT = """set -e
fail() { swapoff /swapfile 2>/dev/null || true; rm -f /swapfile /etc/systemd/system/swapfile.swap; exit 1; }
trap fail ERR
fallocate -l @@SIZE@@M /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=@@SIZE@@ status=none
chmod 600 /swapfile
mkswap /swapfile >/dev/null
cat > /etc/systemd/system/swapfile.swap <<'UNIT'
# Created by Mas (Health → Create a swap file).
[Unit]
Description=Swap file created by Mas

[Swap]
What=/swapfile

[Install]
WantedBy=swap.target
UNIT
systemctl daemon-reload
systemctl enable --now swapfile.swap
trap - ERR
echo 'vm.swappiness = 10' > /etc/sysctl.d/99-mas-swap.conf
sysctl -q -w vm.swappiness=10 || true
swapon --show
"""
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._*-]{0,252}$")
RETIRED_DIR = "/root/letsencrypt-retired"
# Folders searched for programs that still use a certificate (docker compose files, other servers…).
SEARCH_DIRS = ("/etc", "/opt", "/srv")
DISABLED_VHOSTS = "/etc/nginx/sites-available/"


class FixError(Exception):
    pass


def tail(text: str, size: int = 3000) -> str:
    return text.strip()[-size:]


class HealthFixer:
    def __init__(self, host: Host, config: Config, backups_dir: Path) -> None:
        self.host, self.config = host, config
        self.backups = backups_dir / "certbot"

    def apply(self, action: str, target: str) -> dict:
        """{ok, output}; FixError when the fix does not apply (anymore)."""
        if action not in ACTIONS or not NAME_RE.fullmatch(target or ""):
            raise FixError("unknown fix")
        current = run_checks(self.host, self.config, dns=False)
        if not any(c.action == action and c.target == target for c in current):
            raise FixError("this problem is not reported anymore: check again")
        return getattr(self, "_" + action.replace("-", "_"))(target)

    def _run(self, argv: list[str], timeout: float = 120) -> tuple[bool, str]:
        res = self.host.run(argv, timeout=timeout)
        if res is None:
            return False, f"{argv[0]} is missing or timed out"
        return res.ok, (res.stdout + ("\n" + res.stderr if res.stderr else "")).strip()

    # -- certificates --------------------------------------------------------------

    def references(self, name: str) -> list[str]:
        """Files outside certbot that still name the certificate's folder."""
        _, out = self._run(["grep", "-R", "-l", "-F", "-I", "-s", "--exclude-dir=letsencrypt",
                            "--exclude-dir=node_modules", "--exclude-dir=.git", "-e", f"live/{name}/",
                            *(str(self.host.real(d)) for d in SEARCH_DIRS)], timeout=90)
        files = []
        for line in (line.strip() for line in out.splitlines()):
            if line:
                try:
                    files.append(self.host.virtual(Path(line)))
                except ValueError:
                    files.append(line)
        return files

    def _cert_delete(self, name: str) -> dict:
        refs = self.references(name)
        blocking = [f for f in refs if not f.startswith(DISABLED_VHOSTS)]
        if blocking:
            raise FixError(f"still used by: {', '.join(blocking[:10])}. Remove it there first.")
        members = [m for m in (f"live/{name}", f"archive/{name}", f"renewal/{name}.conf")
                   if self.host.exists(f"/etc/letsencrypt/{m}")]
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive = self.backups / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
        ok, out = self._run(["tar", "czf", str(archive), "-C", str(self.host.real("/etc/letsencrypt")), *members])
        if not ok:
            raise FixError(f"could not keep a copy first, nothing deleted: {tail(out, 500)}")
        ok, out = self._run(["certbot", "delete", "--cert-name", name, "--non-interactive"], timeout=120)
        notes = [f"copy kept in {archive}"]
        if refs:
            notes.append(f"disabled vhosts still naming it: {', '.join(refs)}")
        return {"ok": ok, "output": tail(out) + "\n\n" + "\n".join(notes)}

    def _cert_retire(self, name: str) -> dict:
        src = f"{RENEWAL_DIR}/{name}.conf"
        if not self.host.is_file(src):
            raise FixError(f"{src} is already gone")
        self.host.makedirs(RETIRED_DIR, mode=0o700)
        dest = f"{RETIRED_DIR}/{name}.conf.{time.strftime('%Y%m%d-%H%M%S')}"
        self.host.rename(src, dest)
        return {"ok": True, "output": f"{src} moved to {dest}"}

    def _cert_renew_test(self, name: str) -> dict:
        ok, out = self._run(["certbot", "renew", "--cert-name", name, "--dry-run", "--non-interactive"],
                            timeout=300)
        return {"ok": ok, "output": tail(out)}

    def _renewal_nginx(self, name: str) -> dict:
        path = f"{RENEWAL_DIR}/{name}.conf"
        before = self.host.read_text(path)
        if before is None:
            raise FixError(f"{path} is gone")
        backup = self.host.backup(path)
        after = re.sub(r"^authenticator = standalone$", "authenticator = nginx", before, flags=re.M)
        after = re.sub(r"^(pre_hook|post_hook) = .*\n?", "", after, flags=re.M)
        self.host.write_text(path, after, mode=0o644)
        ok, out = self._run(["certbot", "renew", "--cert-name", name, "--dry-run", "--non-interactive"],
                            timeout=300)
        if not ok:
            self.host.write_text(path, before, mode=0o644)
            return {"ok": False, "output": f"the test renewal failed, {path} is restored as it was:\n{tail(out)}"}
        return {"ok": True, "output": f"{path} now renews through nginx (backup: {backup})\n\n{tail(out, 1500)}"}

    # -- services ----------------------------------------------------------------------

    def _swap_create(self, _target: str) -> dict:
        if self.host.exists(SWAP_FILE):
            raise FixError(f"{SWAP_FILE} already exists")
        size = swap_size_mb(meminfo(self.host).get("MemTotal", 0))
        ok, out = self._run(["df", "-Pm", "/"])
        try:
            free = int(out.splitlines()[-1].split()[3])
        except (IndexError, ValueError):
            raise FixError(f"cannot read the free disk space: {tail(out, 300)}") from None
        if free < size + 2048:
            raise FixError(f"not enough disk space: {free} MB free, {size + 2048} MB needed "
                           f"({size} MB of swap and 2 GB kept free)")
        script = SWAP_SCRIPT.replace("@@SIZE@@", str(size))
        ok, out = self._run(["systemd-run", "--quiet", "--wait", "--collect", "--pipe", "bash", "-c", script],
                            timeout=900)
        if not ok:
            return {"ok": False, "output": f"nothing was kept:\n{tail(out)}"}
        return {"ok": True, "output": f"{size // 1024} GB of swap on {SWAP_FILE}, used only when the memory is full"
                                      f"\n\n{tail(out, 1500)}"}

    def _certbot_timer(self, _target: str) -> dict:
        ok, out = self._run(["systemctl", "enable", "--now", "certbot.timer"])
        return {"ok": ok, "output": out or "certbot.timer enabled and started"}

    def _nginx_systemd(self, _target: str) -> dict:
        self._run(["pkill", "-9", "-x", "nginx"])
        time.sleep(1)
        ok, out = self._run(["systemctl", "start", "nginx"])
        return {"ok": ok, "output": out or "nginx started by systemd"}
