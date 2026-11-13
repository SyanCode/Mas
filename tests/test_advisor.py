"""The advisor: proposals read from the AI's answer, kept once, nothing sent but statuses."""

from __future__ import annotations

import io
import json

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.advisor import Advisor, AdvisorError, fingerprint, parse_proposals
from mas.ai import AiPool
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
API = "sk-ant-api03-" + "b" * 40


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(text: str):
    return Resp(json.dumps({"content": [{"type": "text", "text": text}]}).encode())


GOOD = json.dumps({"proposals": [
    {"severity": "warning", "title": "Add a cookie banner", "detail": "Trackers load before consent.", "target": "shop"},
    {"severity": "bogus", "title": "Cap shop memory", "detail": "Shop uses 900 MB.", "target": "shop"},
    {"severity": "info", "title": "", "detail": "dropped: no title"},
]})


@pytest.fixture
def adv(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    pool = AiPool(host, Config(), db)
    return Advisor(db, pool, lambda: {"services": []}), pool, db


def test_parse_drops_bad_proposals():
    out = parse_proposals("Sure!\n" + GOOD + "\nbye")
    assert [p["title"] for p in out] == ["Add a cookie banner", "Cap shop memory"]
    assert out[1]["severity"] == "info"
    with pytest.raises(AdvisorError):
        parse_proposals("no json here")
    assert parse_proposals('{"proposals": []}') == []


def test_run_keeps_proposals_once(adv):
    a, pool, db = adv
    with pytest.raises(AdvisorError, match="connect an AI account"):
        a.run()
    pool.add("Pro", "anthropic", API)
    pool.opener = lambda req, timeout: answer(GOOD)
    assert a.run() == {"added": 2}
    for row in db.list_suggestions():
        db.set_suggestion_status(row["id"], "dismissed")
    db.set_setting("advisor_last_run", "0")
    assert a.run() == {"added": 0}  # dismissed proposals do not come back
    assert len(db.list_suggestions("dismissed")) == 2


def test_errors_are_remembered(adv):
    a, pool, db = adv
    pool.add("Pro", "anthropic", API)
    pool.opener = lambda req, timeout: answer("not json")
    with pytest.raises(AdvisorError):
        a.run()
    assert "could not be read" in a.settings()["error"]


def test_tick_follows_the_interval(adv):
    a, pool, db = adv
    calls = []
    pool.opener = lambda req, timeout: calls.append(1) or answer(GOOD)
    a.tick()
    assert not calls  # no account
    pool.add("Pro", "anthropic", API)
    a.configure("off")
    a.tick()
    assert not calls
    a.configure("daily")
    a.tick()
    a.tick()  # just ran: not again
    assert len(calls) == 1


def test_fingerprint_ignores_case_and_punctuation():
    assert fingerprint("Turn ON swap!", "Shop") == fingerprint("turn on swap", "shop")


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    with TestClient(app) as client:
        r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
        yield client, {"X-CSRF-Token": r.json()["csrf"]}, db


def test_routes(env):
    client, csrf, db = env
    assert client.get("/api/advisor").json()["connected"] is False
    body = {"provider": "anthropic", "secret": API, "name": "Pro"}
    assert client.post("/api/ai/accounts", headers=csrf, json={**body, "password": "wrong"}).status_code == 403
    r = client.post("/api/ai/accounts", headers=csrf, json={**body, "password": PASSWORD})
    assert r.status_code == 200 and API not in r.text and r.json()["accounts"][0]["hint"] == "…bbbb"
    assert client.get("/api/advisor").json()["connected"] is True
    assert client.put("/api/advisor", headers=csrf, json={"interval": "hourly"}).status_code == 400
    sent = []
    client.app.state.pool.opener = lambda req, timeout: sent.append(req.data) or answer(GOOD)
    out = client.post("/api/advisor/run", headers=csrf).json()
    assert out["added"] == 2 and len(out["suggestions"]) == 2
    payload = json.loads(json.loads(sent[0])["messages"][0]["content"])
    assert set(payload) == {"version", "services", "health_checks", "apps"}
    sid = out["suggestions"][0]["id"]
    assert client.put(f"/api/advisor/suggestions/{sid}", headers=csrf, json={"status": "done"}).json()["ok"]
    assert client.put(f"/api/advisor/suggestions/{sid}", headers=csrf, json={"status": "x"}).status_code == 400
    assert client.put("/api/advisor/suggestions/999", headers=csrf, json={"status": "done"}).status_code == 404


def test_api_tokens_cannot_reach_the_ai_routes():
    for path in ("/api/advisor", "/api/advisor/run", "/api/ai/accounts", "/api/robot/runs"):
        assert not auth.api_token_allowed(path)


def test_claude_login_routes(env):
    from pathlib import Path
    client, csrf, db = env
    client.app.state.claude_login.override = str(Path(__file__).parent / "fixtures" / "fake_claude.py")
    assert client.get("/api/ai/claude-login").json()["installed"] is True
    assert client.post("/api/ai/claude-login/start", headers=csrf, json={"password": "wrong"}).status_code == 403
    url = client.post("/api/ai/claude-login/start", headers=csrf, json={"password": PASSWORD}).json()["url"]
    assert url.startswith("https://claude.ai/")
    assert client.post("/api/ai/claude-login/code", headers=csrf, json={"code": "nope-nope"}).status_code == 409
    client.post("/api/ai/claude-login/start", headers=csrf, json={"password": PASSWORD})
    r = client.post("/api/ai/claude-login/code", headers=csrf, json={"code": "GOOD-CODE#1234", "name": "Claude Pro"})
    assert r.status_code == 200 and r.json()["accounts"][0]["kind"] == "claude-code" and "sk-ant-oat" not in r.text
    assert not auth.api_token_allowed("/api/ai/claude-login/start")
