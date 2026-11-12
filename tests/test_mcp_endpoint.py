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


def test_mcp_site_tools_and_the_github_check(env):
    from mas.compliance import Auditor, Response
    client, db, uid, app = env
    csrf = login(client)
    client.put("/api/mcp", headers=csrf, json={"enabled": True})
    read, write = make_token(client, csrf), make_token(client, csrf, write=True)
    anon = TestClient(app)
    db.create_app("site", name="Site", repo="acme/site", branch="main", type="static", domain="site.example.com",
                  installation_id=1)
    tools = {t["name"] for t in rpc(anon, read, "tools/list").json()["result"]["tools"]}
    assert {"site_stats", "compliance_report", "run_compliance_audit", "site_uptime"} <= tools

    def text(res):
        assert not res.get("isError"), res
        return res["structuredContent"] if "structuredContent" in res else json.loads(res["content"][0]["text"])
    assert text(call(anon, read, "compliance_report", {"app_id": "site"}))["audited"] is False
    assert "conversion" in text(call(anon, read, "site_stats", {"app_id": "site"}))

    # an audit of a site that loads a tracker and has no legal pages, posted as a check on the commit
    comp = app.state.compliance
    comp.auditor = Auditor(lambda url, follow=True: Response(url, 200, [], b'<html lang="fr"><script src="https://www.googletagmanager.com/gtag/js"></script></html>')
                           if url == "https://site.example.com/" else Response(url, 404))
    posted = []
    comp.checks = lambda target, release_id, result: posted.append((target["id"], release_id, result["verdict"]))
    denied = call(anon, read, "run_compliance_audit", {"app_id": "site"})
    assert denied["isError"] and "read-only" in denied["content"][0]["text"]
    ran = text(call(anon, write, "run_compliance_audit", {"app_id": "site"}))
    assert ran["verdict"] == "blocking" and ran["counts"]["critical"] >= 3
    report = text(call(anon, read, "compliance_report", {"app_id": "site"}))
    assert report["audited"] and "data, not instructions" in report["note"]
    assert any("Google Tag Manager" in f["title"] and f["fix"] for f in report["to_fix"])
    assert not posted  # a manual audit belongs to no release: no check

    from mas.compliance import check_markdown
    conclusion, title, summary = check_markdown(comp.latest("site"), {"score": 90})
    assert conclusion == "failure" and "since the previous audit" in title and "Google Tag Manager" in summary
    assert "Not legal advice" in summary


def test_check_run_is_posted_after_a_release_and_a_refusal_is_harmless(env):
    from mas.compliance import Auditor, Response
    client, db, uid, app = env
    db.create_app("site", name="Site", repo="acme/site", branch="main", type="static", domain="site.example.com", installation_id=7)
    rid = db.create_release("site", "push", "a", sha="abc1234567890", status="live")
    calls = []

    class Gh:
        def create_check_run(self, *args):
            calls.append(args)
            if len(calls) > 1:
                from mas.github import GitHubError
                raise GitHubError("GitHub API POST /check-runs: HTTP 403 Resource not accessible by integration")
    app.state.github_for_tests = Gh()
    comp = app.state.compliance
    comp.auditor = Auditor(lambda url, follow=True: Response(url, 404))
    comp.checks = lambda target, release_id, result: Gh().create_check_run(target["installation_id"], target["repo"], "abc1234567890", "Mas compliance", "failure", "t", "s")
    comp.run(db.get_app("site"), rid, "release")  # a refusal (403) must not break the audit
    assert comp.latest("site") is not None
