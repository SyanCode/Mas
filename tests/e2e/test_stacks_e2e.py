"""Ready-made services with a real Docker: what the unit tests can only simulate."""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time
from pathlib import Path

import pytest
from e2e_helpers import http_get, port_open, sh

from mas.stacks import StackManager

def imap_login(user: str, password: str, timeout: float = 90) -> None:
    """Log in over IMAPS like a mail app: reconnect while Dovecot starts or reloads its accounts."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            imap = imaplib.IMAP4_SSL("127.0.0.1", 993, ssl_context=TLS, timeout=10)
            assert imap.login(user, password)[0] == "OK"
            imap.logout()
            return
        except (OSError, imaplib.IMAP4.abort, imaplib.IMAP4.error):
            if time.monotonic() > deadline:
                raise
            time.sleep(3)


TLS = ssl.create_default_context()
TLS.check_hostname = False
TLS.verify_mode = ssl.CERT_NONE  # self-signed certificate of the test


def test_any_image_with_web_and_public_ports(env):
    stacks = StackManager(env.host, env.config, env.db, env.deployer)
    created = stacks.create("custom", "e2e-nginx", {"image": "nginx:alpine", "web_port": 80,
                                                     "public_ports": "18089:80", "volume": "/usr/share/nginx/html",
                                                     "env": "E2E=first"}, {}, None, "e2e")
    try:
        Path(stacks.dir("e2e-nginx"), "data").mkdir(parents=True, exist_ok=True)
        page = Path(stacks.dir("e2e-nginx"), "data", "index.html")
        page.write_text("hello from mas e2e")
        page.chmod(0o644)  # what a user puts there; nginx reads it as its own user
        env.deploy(created["release_id"])
        app = env.db.get_app("e2e-nginx")
        status, body = http_get(f"http://127.0.0.1:{app['port']}/")  # web port, on 127.0.0.1 only
        listing = stacks.exec(app, "app", ["ls", "-lan", "/usr/share/nginx/html"]) if status != 200 else ""
        assert status == 200 and "hello from mas e2e" in body, listing  # the kept folder is mounted
        assert http_get("http://127.0.0.1:18089/")[0] == 200  # published port
        assert stacks.exec(app, "app", ["printenv", "E2E"]).strip() == "first"
        assert "ready for start up" in stacks.logs("e2e-nginx")  # nginx's entrypoint says so

        stacks.control("e2e-nginx", "stop")
        assert not port_open(18089)
        stacks.control("e2e-nginx", "start")
        assert http_get("http://127.0.0.1:18089/")[0] == 200

        # new settings: redeployed, the data folder is still there
        env.deploy(stacks.update("e2e-nginx", {"env": "E2E=second"}, {}, "e2e"))
        assert stacks.exec(app, "app", ["printenv", "E2E"]).strip() == "second"
        assert "hello from mas e2e" in http_get(f"http://127.0.0.1:{app['port']}/")[1]
    finally:
        env.deployer.remove_app("e2e-nginx", purge_files=True)
    assert "mas-e2e-nginx" not in sh("docker", "ps", "-a", "--format", "{{.Names}}")
    assert not Path(stacks.dir("e2e-nginx")).exists()
    assert not port_open(18089)


def test_catalog_website(env):
    """Uptime Kuma as the catalog writes it: its page answers on the port nginx would proxy."""
    env.no_web_server()
    stacks = StackManager(env.host, env.config, env.db, env.deployer)
    created = stacks.create("uptime-kuma", "e2e-kuma", {}, {}, "kuma.e2e.test", "e2e")
    try:
        env.deploy(created["release_id"])  # includes the health check on 127.0.0.1:<port>
        status, _ = http_get(f"http://127.0.0.1:{env.db.get_app('e2e-kuma')['port']}/")
        assert status in (200, 302)
    finally:
        env.deployer.remove_app("e2e-kuma", purge_files=True)


@pytest.fixture
def mail_certificate():
    """docker-mailserver reads /etc/letsencrypt/live/<hostname>: a self-signed one stands for certbot's."""
    live = Path("/etc/letsencrypt/live/mail.e2e.test")
    created = not live.exists()
    live.mkdir(parents=True, exist_ok=True)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=mail.e2e.test",
       "-keyout", str(live / "privkey.pem"), "-out", str(live / "fullchain.pem"))
    (live / "cert.pem").write_bytes((live / "fullchain.pem").read_bytes())
    yield live
    if created:
        sh("rm", "-rf", str(live))


def test_mail_server(env, mail_certificate):
    env.no_web_server()
    stacks = StackManager(env.host, env.config, env.db, env.deployer)
    created = stacks.create("mail", "e2e-mail", {"mail_domain": "e2e.test", "mailbox": "contact"}, {},
                            "mail.e2e.test", "e2e")
    try:
        env.deploy(created["release_id"])
        app = env.db.get_app("e2e-mail")
        password = stacks.ctx(app).secrets["mailbox_password"]

        # the DKIM key is where Mas looks for it, and its DNS record is shown
        info = stacks.info("e2e-mail")
        dkim = next(r["value"] for r in info["dns"] if r["name"] == "mail._domainkey.e2e.test")
        assert dkim.startswith("v=DKIM1") and "p=" in dkim, dkim

        # the mailbox works: IMAP over TLS, then SMTP submission with STARTTLS
        imap_login("contact@e2e.test", password)
        with smtplib.SMTP("127.0.0.1", 587, timeout=30) as smtp:
            smtp.starttls(context=TLS)
            smtp.login("contact@e2e.test", password)

        # "Add a mailbox" gives a working address
        output = stacks.action("e2e-mail", "add-mailbox", {"name": "sales"})
        new_password = output.rsplit("Password: ", 1)[1].strip()
        imap_login("sales@e2e.test", new_password)

        # a second deployment does not recreate the mailbox nor the key
        before = sorted(Path(stacks.dir("e2e-mail"), "data", "config", "opendkim", "keys", "e2e.test").iterdir())
        env.deploy(env.deployer.enqueue("e2e-mail", "manual", "e2e"))
        after = sorted(Path(stacks.dir("e2e-mail"), "data", "config", "opendkim", "keys", "e2e.test").iterdir())
        assert [p.read_bytes() for p in before] == [p.read_bytes() for p in after]
        imap_login("contact@e2e.test", password)
    finally:
        env.deployer.remove_app("e2e-mail", purge_files=True)
