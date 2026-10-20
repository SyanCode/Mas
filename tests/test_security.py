"""Regression tests for the security review (one test per finding)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pyotp
import pytest
import server
from fastapi.testclient import TestClient

from mas import auth, templates
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.deploy import DeployError, validate_commands
from mas.host import FixtureHost

PASSWORD = "correct horse battery"


@pytest.fixture
def api(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands["*"] = {"returncode": 0}
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password(PASSWORD))
    config = Config(cookie_secure=False, github_app_id=1, github_webhook_secret="hooksecret",
                    secret_key_path="/etc/mas/secret.key")
    app = create_app(host, config, db, background=False)
    app.state.deployer.enqueue = lambda *a, **k: 1
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]
    return client, db, uid, {"X-CSRF-Token": csrf}


def test_commands_cannot_inject_unit_lines():
    with pytest.raises(DeployError):
        validate_commands(None, "node server.js\nExecStartPre=+/bin/sh -c 'id > /tmp/pwned'", None)
    with pytest.raises(DeployError):
        validate_commands("npm run build\r\nrm -rf /", None, None)
    validate_commands("npm ci && npm run build", ".venv/bin/uvicorn main:app --port $PORT", "dist/site")


@pytest.mark.parametrize("root_dir", ["../../etc", "public/../../..", "/etc", "a//b", "..", "x/.."])
def test_root_dir_cannot_escape_the_release(root_dir):
    with pytest.raises(DeployError):
        validate_commands(None, None, root_dir)


def test_api_rejects_injection_in_app_settings(api):
    client, db, _, h = api
    db.create_app("web", name="web", repo="acme/web", branch="main", type="static")
    assert client.patch("/api/apps/web", json={"root_dir": "../../etc"}, headers=h).status_code == 400
    r = client.patch("/api/apps/web", json={"build_command": "make\nExecStartPre=+/bin/true"}, headers=h)
    assert r.status_code == 400


def test_github_paths_cannot_be_smuggled(api):
    client, *_ = api
    assert client.get("/api/github/branches?repo=../../user&installation_id=1").status_code == 400
    assert client.get("/api/github/detect?repo=acme/web&installation_id=1&ref=main%26x%3Dy").status_code == 400


def test_webhook_delivery_cannot_be_replayed(api):
    client, *_ = api
    raw = json.dumps({"zen": "hi"}).encode()
    sig = "sha256=" + hmac.new(b"hooksecret", raw, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig, "X-GitHub-Delivery": "d-1"}
    assert client.post("/api/github/webhook", content=raw, headers=headers).status_code == 200
    assert client.post("/api/github/webhook", content=raw, headers=headers).status_code == 409


def test_replacing_2fa_needs_the_current_code(api):
    client, db, uid, h = api
    secret = auth.new_totp_secret()
    db.update_user(uid, totp_secret=secret)
    r = client.post("/api/account/totp/setup", json={"password": PASSWORD}, headers=h)
    assert r.status_code == 403
    r = client.post("/api/account/totp/setup", json={"password": PASSWORD, "code": pyotp.TOTP(secret).now()},
                    headers=h)
    assert r.status_code == 200


def test_password_checks_are_rate_limited_inside_a_session(api):
    client, _, _, h = api
    for _ in range(5):
        assert client.post("/api/account/totp/setup", json={"password": "nope"}, headers=h).status_code == 403
    assert client.post("/api/account/totp/setup", json={"password": PASSWORD}, headers=h).status_code == 429


def test_health_does_not_disclose_the_version(api):
    client, *_ = api
    assert client.get("/api/health").json() == {"ok": True}
    headers = client.get("/api/health").headers
    assert headers["cross-origin-opener-policy"] == "same-origin" and "camera=()" in headers["permissions-policy"]


def test_secure_session_cookie_uses_host_prefix(tmp_path):
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(FixtureHost(server.build(tmp_path / "srv")), Config(cookie_secure=True), db, background=False)
    r = TestClient(app, base_url="https://testserver").post("/api/login",
                                                              json={"username": "admin", "password": PASSWORD})
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-mas_session=") and "Secure" in cookie and "Path=/" in cookie


def test_generated_units_are_sandboxed():
    unit = templates.unit("web", "acme/web", "mas-web", "/srv/mas/apps/web/current", "/etc/mas/apps/web.env",
                          7100, "/usr/bin", "/usr/bin/npm start")
    for directive in ("NoNewPrivileges=yes", "CapabilityBoundingSet=\n", "PrivateDevices=yes", "ProtectHome=yes",
                      "RestrictSUIDSGID=yes", "User=mas-web"):
        assert directive in unit
