"""Notifications: mail server and webhook settings, events, failed deployments and the weekly digest."""

from __future__ import annotations

import json
from datetime import datetime

import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, security):
        self.args = (host, port, security)
        self.logged = None

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, msg):
        FakeSMTP.sent.append((self.args, self.logged, msg))

    def quit(self):
        pass


def make(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com", installation_id=1)
    api = create_app(host, Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key"), db, background=False)
    n = api.state.notifier
    FakeSMTP.sent = []
    n.smtp_factory = FakeSMTP
    hooks = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    n.opener = lambda req, timeout=0: (hooks.append((req.full_url, json.loads(req.data))), Resp())[1]
    client = TestClient(api)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    return client, db, n, hooks, {"x-csrf-token": csrf}


def test_settings_are_validated_and_secrets_sealed(tmp_path):
    client, db, n, hooks, h = make(tmp_path)
    r = client.put("/api/notify/smtp", headers=h, json={"host": "smtp.example.com", "port": 587, "security": "starttls",
                                                         "user": "me", "password": "s3cret", "from": "noreply@example.com"})
    assert r.status_code == 200 and r.json()["has_password"] and "password" not in r.json()
    assert "s3cret" not in db.get_setting("smtp")  # encrypted at rest
    assert client.put("/api/notify/smtp", headers=h, json={"host": "x", "port": 70000, "from": "a@b.co"}).status_code == 400
    assert client.put("/api/notify/smtp", headers=h, json={"host": "x", "from": "nope"}).status_code == 400
    kept = client.put("/api/notify/smtp", headers=h, json={"host": "smtp.example.com", "port": 587, "from": "noreply@example.com"})
    assert kept.json()["has_password"]  # an empty password field keeps the old one

    r = client.put("/api/cms/sites/shop/notify", headers=h, json={"emails": ["client@shop.fr"], "webhook": "https://hooks.slack.com/services/T/B/X",
                                                                  "events": ["compliance", "uptime"], "digest": True})
    assert r.status_code == 200 and r.json()["has_webhook"] and r.json()["webhook"] == ""  # never sent back
    assert "hooks.slack.com" not in db.get_setting("notify:shop")
    for bad in ({"emails": ["not-an-email"]}, {"webhook": "http://insecure.example"}, {"events": ["everything"]}):
        assert client.put("/api/cms/sites/shop/notify", headers=h, json={"events": [], **bad}).status_code == 400


def test_events_reach_webhook_and_email_only_when_chosen(tmp_path):
    client, db, n, hooks, h = make(tmp_path)
    client.put("/api/notify/smtp", headers=h, json={"host": "smtp.example.com", "user": "me", "password": "pw", "from": "noreply@example.com"})
    client.put("/api/cms/sites/shop/notify", headers=h, json={"emails": ["client@shop.fr"], "webhook": "https://hooks.example.com/x", "events": ["compliance"]})
    assert n.notify("shop", "compliance", "Compliance got worse", "92 → 61") == ["webhook", "email"]
    assert hooks[0][1]["event"] == "compliance" and "Compliance got worse" in hooks[0][1]["text"] and "content" in hooks[0][1]
    (args, logged, msg), = FakeSMTP.sent
    assert logged == ("me", "pw") and msg["To"] == "client@shop.fr" and "92 → 61" in msg.get_content()
    assert n.notify("shop", "uptime", "Down", "x") == []  # not chosen
    assert client.post("/api/cms/sites/shop/notify/test", headers=h).json() == {"ok": True}
    assert len(hooks) == 2 and len(FakeSMTP.sent) == 2


def test_failed_deployments_and_the_weekly_digest(tmp_path):
    client, db, n, hooks, h = make(tmp_path)
    client.put("/api/cms/sites/shop/notify", headers=h, json={"webhook": "https://hooks.example.com/x", "events": ["deploy_failed"], "digest": True})
    old = db.create_release("shop", "push", "a", status="failed")
    assert n.tick(datetime(2026, 11, 11, 7, 0).timestamp()) == []  # first look: old failures are not news
    new = db.create_release("shop", "cms", "marie", status="failed")
    db.update_release(new, error="npm run build failed")
    done = n.tick(datetime(2026, 11, 11, 7, 5).timestamp())
    assert done == ["failed:shop"] and "npm run build failed" in hooks[-1][1]["message"]
    assert n.tick(datetime(2026, 11, 11, 7, 10).timestamp()) == []  # told once
    assert old < new
    # digest: Monday morning, once a week
    assert n.tick(datetime(2026, 11, 9, 7, 59).timestamp()) == []  # a Monday, too early
    assert n.tick(datetime(2026, 11, 9, 8, 30).timestamp()) == ["digest:shop"]
    assert "Weekly report" in hooks[-1][1]["title"] and "Changes this week" in hooks[-1][1]["message"]
    assert n.tick(datetime(2026, 11, 9, 9, 0).timestamp()) == []


def test_compliance_drop_notifies(tmp_path):
    from mas.compliance import Auditor, Response
    client, db, n, hooks, h = make(tmp_path)
    client.put("/api/cms/sites/shop/notify", headers=h, json={"webhook": "https://hooks.example.com/x", "events": ["compliance"]})
    good = '<html lang="fr"><body><a href="/m">Mentions légales</a><a href="/c">Confidentialité</a></body></html>'
    bad = '<html lang="fr"><head><script src="https://www.googletagmanager.com/gtag/js"></script></head><body></body></html>'
    page = {"body": good}
    HEAD = [("strict-transport-security", "x"), ("content-security-policy", "x"), ("x-content-type-options", "x"), ("referrer-policy", "x")]

    def fetch(url, follow=True):
        if url.startswith("http://"):
            return Response(url, 301, [("location", "https:" + url[5:])])
        if url == "https://shop.example.com/":
            return Response(url, 200, HEAD, page["body"].encode())
        return Response(url, 200, HEAD, b"<html>page</html>") if url.endswith(("/m", "/c")) else Response(url, 404)
    comp = client.app.state.compliance
    comp.auditor = Auditor(fetch, mas_banner=lambda host: {"enabled": True, "policy_url": "/c"})
    app = db.get_app("shop")
    comp.run(app, None, "manual")
    assert hooks == []  # the first audit has nothing to compare with
    page["body"] = bad
    comp.run(app, None, "manual")
    assert hooks and "NEW blocking issue" in hooks[-1][1]["message"]
