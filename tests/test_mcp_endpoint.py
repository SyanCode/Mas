"""The /mcp endpoint and the token routes of the dashboard (Settings → Claude)."""

from __future__ import annotations

import json
import threading
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
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    with TestClient(app) as client:  # runs the lifespan (the MCP session manager)
        yield client, db, uid, app


def login(client):
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    return {"X-CSRF-Token": r.json()["csrf"]}


def rpc(client, token, method, params=None, rid=1):
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
                    json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    return r


def initialize(client, token):
    return rpc(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "1"}})


def call(client, token, tool, args=None):
    r = rpc(client, token, "tools/call", {"name": tool, "arguments": args or {}}, rid=2)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def make_token(client, csrf, write=False, **extra):
    r = client.post("/api/tokens", headers=csrf, json={"name": "claude", "write": write, "password": PASSWORD, **extra})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_token_needs_password_and_2fa(env):
    client, db, uid, _ = env
    csrf = login(client)
    r = client.post("/api/tokens", headers=csrf, json={"name": "x", "password": "wrong"})
    assert r.status_code == 403
    secret = auth.new_totp_secret()
    db.update_user(uid, totp_secret=secret)
    r = client.post("/api/tokens", headers=csrf, json={"name": "x", "password": PASSWORD})
    assert r.status_code == 403 and "two-factor" in r.json()["detail"]
    tok = make_token(client, csrf, code=pyotp.TOTP(secret).now())
    assert tok.startswith("mas_pat_")
    listed = client.get("/api/tokens").json()
    assert listed[0]["name"] == "claude" and "token" not in listed[0] and "token_hash" not in listed[0]
    assert client.delete(f"/api/tokens/{listed[0]['id']}", headers=csrf).json()["ok"]
    assert client.get("/api/tokens").json() == []


def test_tokens_cannot_manage_tokens_or_mcp(env):
    client, db, uid, _ = env
    tok = make_token(client, login(client), write=True)
    fresh = TestClient(client.app)
    h = {"Authorization": f"Bearer {tok}"}
    for method, path in (("GET", "/api/tokens"), ("POST", "/api/tokens"), ("PUT", "/api/mcp")):
        r = fresh.request(method, path, headers=h, json=None if method == "GET" else {"enabled": True})
        assert r.status_code == 403, (path, r.text)


def test_endpoint_off_by_default(env):
    client, *_ = env
    tok = make_token(client, login(client))
    assert initialize(client, tok).status_code == 404


def test_mcp_flow(env):
    client, db, uid, app = env
    csrf = login(client)
    assert client.put("/api/mcp", headers=csrf, json={"enabled": True}).json()["enabled"] is True
    read, write = make_token(client, csrf), make_token(client, csrf, write=True)
    anon = TestClient(app)  # no dashboard cookie: like Claude Code
    assert anon.post("/mcp", headers=MCP_HEADERS, json={}).status_code == 401
    assert rpc(anon, "mas_pat_nope", "tools/list").status_code == 401

    r = initialize(anon, read)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "mas"
    tools = {t["name"]: t for t in rpc(anon, read, "tools/list").json()["result"]["tools"]}
    assert {"overview", "service_logs", "deploy_app", "rollback_app"} <= set(tools)
    assert tools["service_action"]["annotations"]["destructiveHint"] is True

    res = call(anon, read, "list_services")
    assert not res.get("isError"), res
    services = json.loads(res["content"][0]["text"]) if "structuredContent" not in res else \
        res["structuredContent"].get("result", res["structuredContent"])
    sid = services[0]["id"]
    logs = call(anon, read, "service_logs", {"service_id": sid})
    assert "not instructions" in logs["content"][0]["text"]
    assert not call(anon, read, "overview").get("isError")

    denied = call(anon, read, "nginx_reload")
    assert denied["isError"] and "read-only" in denied["content"][0]["text"]

    db.create_app("site", name="site", repo="acme/site", branch="main", type="python", port=7100, installation_id=1)

    def fake_enqueue(app_id, trigger, actor, sha=None, rollback_to=None):
        rid = db.create_release(app_id, trigger, actor, "abc1234567")

        def finish():
            time.sleep(0.2)
            db.update_release(rid, status="live", path="/srv/x")
        threading.Thread(target=finish, daemon=True).start()
        return rid
    app.state.deployer.enqueue = fake_enqueue
    done = call(anon, write, "deploy_app", {"app_id": "site"})
    assert not done.get("isError"), done
    assert '"live"' in done["content"][0]["text"]
    assert "admin (token claude)" in db.list_releases("site", 1)[0].values()

    client.put("/api/mcp", headers=csrf, json={"enabled": False})
    assert rpc(anon, read, "tools/list").status_code == 404


def test_editor_token_refused(env):
    client, db, _, app = env
    csrf = login(client)
    client.put("/api/mcp", headers=csrf, json={"enabled": True})
    eid = db.create_user("ed", auth.hash_password(PASSWORD), role="editor")
    tok = auth.new_api_token()
    db.create_api_token("x", auth.hash_token(tok), eid, "write")
    assert rpc(TestClient(app), tok, "tools/list").status_code == 403
