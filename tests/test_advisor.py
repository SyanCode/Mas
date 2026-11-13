"""The Claude advisor: token kept encrypted, proposals checked and kept once, nothing sent but statuses."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.advisor import Advisor, AdvisorError, fingerprint, parse_proposals, token_kind
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
OAUTH = "sk-ant-oat01-" + "a" * 40
API = "sk-ant-api03-" + "b" * 40


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(text: str):
    return Resp(json.dumps({"content": [{"type": "text", "text": text}]}).encode())


GOOD = json.dumps({"proposals": [
    {"severity": "warning", "title": "Turn on swap", "detail": "No swap and 90% memory.", "target": ""},
    {"severity": "bogus", "title": "Cap shop memory", "detail": "Shop uses 900 MB.", "target": "shop"},
    {"severity": "info", "title": "", "detail": "dropped: no title"},
]})


@pytest.fixture
def adv(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    a = Advisor(host, Config(), db, lambda: {"services": []})
    return a, db, host


def test_token_kinds():
    assert token_kind(OAUTH) == "claude-code" and token_kind(API) == "api"


def test_parse_drops_bad_proposals():
    out = parse_proposals("Sure!\n" + GOOD + "\nbye")
    assert [p["title"] for p in out] == ["Turn on swap", "Cap shop memory"]
    assert out[1]["severity"] == "info"
    with pytest.raises(AdvisorError):
        parse_proposals("no json here")
    assert parse_proposals('{"proposals": []}') == []


def test_token_is_encrypted_and_hidden(adv):
    a, db, host = adv
    with pytest.raises(AdvisorError):
        a.configure(token="not-a-token")
    a.configure(token=OAUTH, interval="weekly")
    assert OAUTH not in db.get_setting("advisor_token")
    s = a.settings()
    assert s["connected"] and s["kind"] == "claude-code" and s["hint"] == "…aaaa" and s["interval"] == "weekly"
    assert OAUTH not in json.dumps(s)
    a.configure(token="")
    assert not a.settings()["connected"]


def test_run_sends_the_right_headers_and_keeps_proposals_once(adv):
    a, db, _ = adv
    seen = []

    def opener(req, timeout):
        seen.append(req)
        return answer(GOOD)
    a.opener = opener
    a.configure(token=OAUTH)
    assert a.run() == {"added": 2}
    req = seen[0]
    assert req.get_header("Authorization") == f"Bearer {OAUTH}" and "oauth" in req.get_header("Anthropic-beta")
    assert "You are Claude Code" in json.loads(req.data)["system"][0]["text"]
    for row in db.list_suggestions():
        db.set_suggestion_status(row["id"], "dismissed")
    db.set_setting("advisor_last_run", "0")
    assert a.run() == {"added": 0}  # dismissed proposals do not come back
    assert len(db.list_suggestions("dismissed")) == 2
    a.configure(token=API)
    db.set_setting("advisor_last_run", "0")
    a.run()
    assert seen[-1].get_header("X-api-key") == API and seen[-1].get_header("Authorization") is None


def test_errors_are_remembered(adv):
    a, db, _ = adv
    a.configure(token=API)

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    a.opener = refuse
    with pytest.raises(AdvisorError, match="refused the token"):
        a.run()
    assert "refused" in a.settings()["error"]


def test_tick_follows_the_interval(adv):
    a, db, _ = adv
    calls = []
    a.opener = lambda req, timeout: calls.append(1) or answer(GOOD)
    a.tick()
    assert not calls  # no token
    a.configure(token=API, interval="off")
    a.tick()
    assert not calls
    a.configure(interval="daily")
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
    r = client.put("/api/advisor", headers=csrf, json={"token": API, "password": "wrong"})
    assert r.status_code == 403
    r = client.put("/api/advisor", headers=csrf, json={"token": API, "password": PASSWORD})
    assert r.json()["connected"] and API not in r.text
    assert client.put("/api/advisor", headers=csrf, json={"interval": "hourly"}).status_code == 400
    # the summary sent to Claude holds statuses only
    sent = []
    client.app.state.advisor.opener = lambda req, timeout: sent.append(req.data) or answer(GOOD)
    out = client.post("/api/advisor/run", headers=csrf).json()
    assert out["added"] == 2 and len(out["suggestions"]) == 2
    payload = json.loads(json.loads(sent[0])["messages"][0]["content"])
    assert set(payload) == {"version", "services", "health_checks", "apps"}
    sid = out["suggestions"][0]["id"]
    assert client.put(f"/api/advisor/suggestions/{sid}", headers=csrf, json={"status": "done"}).json()["ok"]
    assert client.put(f"/api/advisor/suggestions/{sid}", headers=csrf, json={"status": "x"}).status_code == 400
    assert client.put("/api/advisor/suggestions/999", headers=csrf, json={"status": "done"}).status_code == 404


def test_api_tokens_cannot_reach_the_advisor():
    assert not auth.api_token_allowed("/api/advisor")
    assert not auth.api_token_allowed("/api/advisor/run")
