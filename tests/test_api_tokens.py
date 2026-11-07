"""Personal API tokens: bearer auth for scripts and MCP agents."""

from __future__ import annotations

import time

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password("correct horse battery"))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    return TestClient(app), db, uid


def make(db, uid, scope="read", expires=None, name="claude"):
    token = auth.new_api_token()
    db.create_api_token(name, auth.hash_token(token), uid, scope, expires)
    return {"Authorization": f"Bearer {token}"}


def test_read_token_reads_but_cannot_act(env):
    client, db, uid = env
    h = make(db, uid)
    assert client.get("/api/services", headers=h).status_code == 200
    r = client.post("/api/nginx/reload", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "read-only API token"


def test_write_token_needs_no_csrf(env):
    client, db, uid = env
    h = make(db, uid, "write")
    r = client.post("/api/scan", headers=h)
    assert r.status_code == 200, r.text


def test_actions_are_audited_with_token_name(env):
    client, db, uid = env
    h = make(db, uid, "write", name="mcp")
    svc = client.get("/api/services", headers=h).json()["services"][0]["id"]
    client.patch(f"/api/services/{svc}", headers=h, json={"notes": "hi"})
    s = client.get("/api/session", headers=h).json()
    assert s["username"] == "admin (token mcp)"


def test_unknown_expired_and_revoked(env):
    client, db, uid = env
    bad = {"Authorization": "Bearer " + auth.new_api_token()}
    assert client.get("/api/services", headers=bad).status_code == 401
    old = make(db, uid, expires=time.time() - 1)
    assert client.get("/api/services", headers=old).status_code == 401
    h = make(db, uid)
    tid = db.list_api_tokens()[-1]["id"]
    db.delete_api_token(tid)
    assert client.get("/api/services", headers=h).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("post", "/api/account/password"), ("post", "/api/account/totp/disable"), ("get", "/api/users"),
    ("post", "/api/invites"), ("post", "/api/logout"), ("get", "/api/supabase/credentials"),
    ("get", "/api/apps/x/env"), ("put", "/api/apps/x/env"), ("post", "/api/cluster/tokens"),
])
def test_token_never_reaches_accounts_or_secrets(env, method, path):
    client, db, uid = env
    h = make(db, uid, "write")
    r = client.request(method.upper(), path, headers=h, json=None if method == "get" else {})
    assert r.status_code == 403 and r.json()["detail"] == "not available with an API token"


def test_editor_token_stays_editor(env):
    client, db, _ = env
    eid = db.create_user("ed", auth.hash_password("correct horse battery"), role="editor")
    h = make(db, eid, "write")
    assert client.get("/api/services", headers=h).status_code == 403


def test_deleting_user_deletes_tokens(env):
    client, db, _ = env
    eid = db.create_user("tmp", auth.hash_password("correct horse battery"))
    h = make(db, eid)
    db.delete_user("tmp")
    assert client.get("/api/services", headers=h).status_code == 401


def test_bruteforce_is_rate_limited(env):
    client, *_ = env
    codes = [client.get("/api/services", headers={"Authorization": "Bearer " + auth.new_api_token()}).status_code
             for _ in range(22)]
    assert codes[-1] == 429


def test_cluster_bearer_untouched(env):
    client, *_ = env
    r = client.get("/api/services", headers={"Authorization": "Bearer something-else"})
    assert r.status_code == 401 and r.json()["detail"] == "not authenticated"
