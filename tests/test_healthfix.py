"""One-click fixes from the Health page: only reported problems, with safeguards and backups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
DELETE = ["certbot", "delete", "--cert-name", "old.example.com", "--non-interactive"]


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    now = datetime.now(timezone.utc)

    def lineage(name, conf, expires):
        host.write_text(f"/etc/letsencrypt/renewal/{name}.conf", conf)
        host.write_text(f"/etc/letsencrypt/live/{name}/fullchain.pem", server._cert(name, expires).decode())
        host.write_text(f"/etc/letsencrypt/live/{name}/cert.pem", "x")

    host.symlinks.add("cert.pem")
    lineage("old.example.com", "authenticator = nginx\n", now + timedelta(days=60))
    lineage("mail.example.com", "authenticator = standalone\npre_hook = systemctl stop nginx\n"
                                "post_hook = systemctl start nginx\nfullchain = x\n", now + timedelta(days=70))
    host.write_text("/etc/letsencrypt/renewal/gone.example.com.conf", "authenticator = nginx\n")
    host._commands = {"nginx -t": {"returncode": 0}, "systemctl is-active certbot.timer": {"output": "inactive\n"},
                      **host._commands}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]
    return client, host, db, {"X-CSRF-Token": csrf}


def fix(client, h, action, target):
    return client.post("/api/health/fix", json={"action": action, "target": target}, headers=h)


def test_checks_carry_their_fix(env):
    client, host, db, h = env
    checks = client.get("/api/health/checks", params={"refresh": 1}).json()["checks"]
    unused = next(c for c in checks if c["title"] == "old.example.com: not used by any enabled site")
    assert unused["action"]["id"] == "cert-delete" and unused["target"] == "old.example.com"
    assert unused["action"]["danger"] and unused["action"]["label"] == "Delete the certificate"
    assert {"cert-delete", "cert-retire", "renewal-nginx", "certbot-timer"} <= {
        c["action"]["id"] for c in checks if c["action"]}


def test_delete_an_unused_certificate(env):
    client, host, db, h = env
    # still referenced by a docker compose file: refused, nothing touched
    host._commands["grep -R *"] = {"output": f"{host.real('/opt/proxy/docker-compose.yml')}\n"}
    r = fix(client, h, "cert-delete", "old.example.com")
    assert r.status_code == 400 and "/opt/proxy/docker-compose.yml" in r.json()["detail"]
    assert not any(a[0] in ("tar", "certbot") for a in host.executed)

    # only a disabled vhost names it: a copy is kept first, then certbot deletes it
    host._commands["grep -R *"] = {"output": f"{host.real('/etc/nginx/sites-available/old')}\n"}
    host._commands["tar czf *"] = {"returncode": 0}
    host._commands[" ".join(DELETE)] = {"output": "Deleted all files"}
    r = fix(client, h, "cert-delete", "old.example.com")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and "copy kept in" in r.json()["output"] and "sites-available/old" in r.json()["output"]
    tar = next(i for i, a in enumerate(host.executed) if a[0] == "tar")
    assert host.executed[tar][-2:] == ["live/old.example.com", "renewal/old.example.com.conf"]
    assert host.executed.index(DELETE) > tar
    assert any(e["action"] == "fix-cert-delete" and e["ok"] for e in db.history(None, 20))

    # the copy fails: nothing is deleted
    host.executed.clear()
    host._commands["tar czf *"] = {"returncode": 2, "stderr": "No space left on device"}
    r = fix(client, h, "cert-delete", "old.example.com")
    assert r.status_code == 400 and "nothing deleted" in r.json()["detail"]
    assert DELETE not in host.executed


def test_only_reported_problems_can_be_fixed(env):
    client, host, db, h = env
    for action, target in (("cert-delete", "app.example.com"), ("rm-rf", "old.example.com"),
                           ("cert-delete", "old.example.com; reboot"), ("cert-retire", "old.example.com")):
        assert fix(client, h, action, target).status_code == 400, (action, target)
    assert not any(a[0] in ("certbot", "tar") for a in host.executed)
    assert any(e["action"] == "fix-cert-delete" and not e["ok"] for e in db.history(None, 20))


def test_retire_switch_and_timer(env):
    client, host, db, h = env
    r = fix(client, h, "cert-retire", "gone.example.com")
    assert r.status_code == 200 and r.json()["ok"]
    assert not host.exists("/etc/letsencrypt/renewal/gone.example.com.conf")
    assert any(n.startswith("gone.example.com.conf.") for n in host.listdir("/root/letsencrypt-retired"))
    assert fix(client, h, "cert-retire", "gone.example.com").status_code == 400  # fixed: not reported anymore

    # the test renewal fails: the file is put back as it was
    conf = "/etc/letsencrypt/renewal/mail.example.com.conf"
    before = host.read_text(conf)
    host._commands["certbot renew --cert-name mail.example.com *"] = {"returncode": 1, "stderr": "Challenge failed"}
    r = fix(client, h, "renewal-nginx", "mail.example.com")
    assert r.status_code == 200 and not r.json()["ok"] and "restored" in r.json()["output"]
    assert host.read_text(conf) == before
    host._commands["certbot renew --cert-name mail.example.com *"] = {"output": "simulated renewal succeeded"}
    assert fix(client, h, "renewal-nginx", "mail.example.com").json()["ok"]
    assert host.read_text(conf) == "authenticator = nginx\nfullchain = x\n"
    assert any(".mas-bak-" in n for n in host.listdir("/etc/letsencrypt/renewal"))

    host._commands["systemctl enable --now certbot.timer"] = {"returncode": 0}
    assert fix(client, h, "certbot-timer", "certbot.timer").json()["ok"]


def test_swap_file_on_a_small_server(env):
    client, host, db, h = env
    host.write_text("/proc/meminfo", "MemTotal:  2000000 kB\nMemAvailable: 300000 kB\nSwapTotal: 0 kB\n")
    checks = client.get("/api/health/checks", params={"refresh": 1}).json()["checks"]
    swap = next(c for c in checks if c["category"] == "memory")
    assert swap["level"] == "warning" and swap["action"]["id"] == "swap-create" and "1.9 GB" in swap["title"]

    host._commands["df -Pm /"] = {"output": "Filesystem 1M-blocks Used Available Use% Mounted on\n/dev/vda1 20000 "
                                            "18500 1500 93% /\n"}
    r = fix(client, h, "swap-create", "swapfile")
    assert r.status_code == 400 and "not enough disk space" in r.json()["detail"]  # 2 GB stay free

    host._commands["df -Pm /"]["output"] = ("Filesystem 1M-blocks Used Available Use% Mounted on\n"
                                            "/dev/vda1 20000 5000 15000 25% /\n")
    host._commands["systemd-run *"] = {"output": "NAME      TYPE SIZE USED PRIO\n/swapfile file   2G   0B   -2\n"}
    r = fix(client, h, "swap-create", "swapfile")
    assert r.status_code == 200 and r.json()["ok"], r.json()
    run = next(a for a in host.executed if a[0] == "systemd-run")
    assert run[-3:-1] == ["bash", "-c"] and "fallocate -l 2048M /swapfile" in run[-1]
    assert "vm.swappiness = 10" in run[-1] and "WantedBy=swap.target" in run[-1]

    # swap on, or a big server: nothing to report
    host.write_text("/proc/meminfo", "MemTotal:  2000000 kB\nSwapTotal: 2097148 kB\n")
    assert not [c for c in client.get("/api/health/checks", params={"refresh": 1}).json()["checks"]
                if c["category"] == "memory"]
    host.write_text("/proc/meminfo", "MemTotal: 16000000 kB\nSwapTotal: 0 kB\n")
    assert not [c for c in client.get("/api/health/checks", params={"refresh": 1}).json()["checks"]
                if c["category"] == "memory"]
