"""HTTP API: auth, 2FA, CSRF, rate limiting, services, actions."""

from __future__ import annotations

import time

import pyotp
import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    return TestClient(app), db, uid, host


def login(client, otp=None):
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD, "otp": otp})
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


def test_requires_auth(env):
    client, *_ = env
    assert client.get("/api/services").status_code == 401
    assert client.get("/api/health").json()["ok"] is True


def test_login_sets_strict_httponly_cookie(env):
    client, *_ = env
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/services").status_code == 200


def test_wrong_password_and_unknown_user(env):
    client, *_ = env
    for user in ("admin", "nobody"):
        r = client.post("/api/login", json={"username": user, "password": "nope"})
        assert r.status_code == 401 and r.json()["detail"] == "invalid"


def test_rate_limit(env):
    client, *_ = env
    for _ in range(5):
        client.post("/api/login", json={"username": "admin", "password": "wrong"})
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 429


def test_totp_login_flow_and_replay(env):
    client, db, uid, _ = env
    secret = auth.new_totp_secret()
    db.update_user(uid, totp_secret=secret)
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["detail"] == "otp_required"
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD, "otp": "000000"})
    assert r.json()["detail"] == "otp_invalid"
    code = pyotp.TOTP(secret).now()
    login(client, code)
    # the same code cannot be used twice
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD, "otp": code})
    assert r.json()["detail"] == "otp_invalid"


def test_totp_enrollment_from_dashboard(env):
    client, db, uid, _ = env
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/account/totp/setup", json={"password": "bad"}, headers=h).status_code == 403
    setup = client.post("/api/account/totp/setup", json={"password": PASSWORD}, headers=h).json()
    assert setup["uri"].startswith("otpauth://totp/Mas:admin") and "<svg" in setup["qr_svg"]
    assert client.post("/api/account/totp/enable", json={"code": "123456"}, headers=h).status_code == 400
    code = pyotp.TOTP(setup["secret"]).now()
    assert client.post("/api/account/totp/enable", json={"code": code}, headers=h).status_code == 200
    assert db.get_user("admin")["totp_secret"] == setup["secret"]
    assert client.get("/api/session").json()["totp_enabled"] is True


def test_csrf_and_origin_checks(env):
    client, *_ = env
    csrf = login(client)
    assert client.post("/api/scan").status_code == 403
    assert client.post("/api/scan", headers={"X-CSRF-Token": "forged"}).status_code == 403
    r = client.post("/api/scan", headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/scan", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_services_and_detail(env):
    client, *_ = env
    login(client)
    data = client.get("/api/services").json()
    by_id = {s["id"]: s for s in data["services"]}
    assert by_id["api.example.com"]["actions"] == ["start", "stop", "restart", "reload", "enable", "disable"]
    assert by_id["api.example.com"]["confirm_actions"] == ["disable", "restart", "stop"]
    assert by_id["docs.example.com"]["actions"] == ["reload"]
    assert by_id["unknown-python3"]["actions"] == []
    assert client.get("/api/services/nope").status_code == 404
    assert "history" in client.get("/api/services/api.example.com").json()


def test_action_runs_allowlisted_command_and_is_audited(env):
    client, db, _, host = env
    h = {"X-CSRF-Token": login(client)}
    host._commands["systemctl restart api.service"] = {"returncode": 0}
    r = client.post("/api/services/api.example.com/actions/restart", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert ["systemctl", "restart", "api.service"] in host.executed
    assert client.post("/api/services/api.example.com/actions/rm", headers=h).status_code == 400
    assert client.post("/api/services/unknown-python3/actions/stop", headers=h).status_code == 400
    actions = [(e["action"], e["ok"]) for e in db.history("api.example.com")]
    assert ("restart", True) in actions and ("rm", False) in actions


def test_nginx_reload_refused_when_config_is_invalid(env):
    client, _, _, host = env
    h = {"X-CSRF-Token": login(client)}
    host._commands["nginx -t"] = {"returncode": 1, "stderr": "nginx: [emerg] unexpected '}'"}
    r = client.post("/api/nginx/reload", headers=h)
    assert r.status_code == 409 and "emerg" in r.json()["detail"]
    assert ["systemctl", "reload", "nginx"] not in host.executed

    host._commands["nginx -t"] = {"returncode": 0, "stderr": "syntax is ok"}
    host._commands["systemctl reload nginx"] = {"returncode": 0}
    assert client.post("/api/nginx/reload", headers=h).status_code == 200
    assert ["systemctl", "reload", "nginx"] in host.executed


def test_patch_labels_and_type_override(env):
    client, *_ = env
    h = {"X-CSRF-Token": login(client)}
    r = client.patch("/api/services/shop.example.com", json={"display_name": "Old shop", "type": "php"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Old shop" and body["type"] == "php" and body["type_source"] == "manual"
    body = client.patch("/api/services/shop.example.com", json={"type": None}, headers=h).json()
    assert body["type"] == "unknown" and body["type_source"] == "detected" and body["display_name"] == "Old shop"
    assert client.patch("/api/services/shop.example.com", json={"type": "cobol"}, headers=h).status_code == 422


def test_logout_and_expired_session(env):
    client, db, *_ = env
    h = {"X-CSRF-Token": login(client)}
    assert client.post("/api/logout", headers=h).status_code == 200
    assert client.get("/api/services").status_code == 401

    login(client)
    with db.tx() as c:
        c.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
    assert client.get("/api/services").status_code == 401


def test_password_change_signs_out_other_sessions(env):
    client, *_ = env
    other = TestClient(client.app)
    login(other)
    h = {"X-CSRF-Token": login(client)}
    r = client.post("/api/account/password", json={"current": PASSWORD, "new": "short"}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/account/password", json={"current": PASSWORD, "new": "another long password"}, headers=h)
    assert r.status_code == 200
    assert other.get("/api/services").status_code == 401
    assert client.get("/api/services").status_code == 200


def test_security_headers(env):
    client, *_ = env
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_stats_of_a_service_and_restart_all_plan(env):
    client, db, _, host = env
    csrf = login(client)
    r = client.get("/api/services/blog.example.com/stats?days=7")
    assert r.status_code == 200 and r.json()["shared_log"] is True
    assert client.get("/api/services/systemd-mariadb/stats").json()["reason"] == "no-domain"
    host._commands = {"systemctl is-active *": {"output": "inactive\n"}, **host._commands}
    plan = client.get("/api/restart-all").json()
    assert plan["running"] is False and any(i["key"] == "unit:mariadb.service" for i in plan["plan"])
    assert client.post("/api/restart-all", json={"skip": []}).status_code in (401, 403)  # CSRF needed
    assert csrf
