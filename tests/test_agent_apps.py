"""Apps created by an agent (MCP): plan_app / create_app, allowed repositories, waiting for variables."""

from __future__ import annotations

import json

import pytest
import server
from fastapi.testclient import TestClient
from test_dns import CREDS, FakeOvh

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.dns import IPV4_KEY, save_providers
from mas.github import GitHubError
from mas.host import FixtureHost
from mas.models import ServiceType

IP = "203.0.113.7"
SECRET_DEFAULT = "do-not-leak-0f3c9a"  # a value written in .env.example for a secret-looking key
ENV_EXAMPLE = f"""# Secrets: generate each one with openssl rand -hex 32
SIGNING_SECRET=
ADMIN_KEY=change-me

# The database lives outside the releases
DB_PATH=/srv/mas/apps/indiepolis/.home/data/indiepolis.db
SMTP_HOST=ssl0.ovh.net
SMTP_PORT=465
SMTP_PASS=
API_TOKEN={SECRET_DEFAULT}
MAIL_FROM="Indiepolis <hello@indiepolis.com>"
PORT=3000

# STRIPE_SECRET_KEY=
"""
NAMES = {"SIGNING_SECRET", "ADMIN_KEY", "SMTP_PASS", "API_TOKEN"}


class FakeGitHub:
    def __init__(self):
        self.repos = {"SyanCode/Indiepolis": ENV_EXAMPLE, "SyanCode/plain": "TITLE=Hello\n",
                      "Other/thing": "A=1\n"}
        self.calls: list[str] = []

    def installation_for(self, repo):
        if repo not in self.repos:
            raise GitHubError("Not Found")
        return 42

    def branches(self, installation_id, repo):
        return ["main", "staging"]

    def detect_type(self, installation_id, repo, ref):
        return ServiceType.NODE

    def read_file(self, installation_id, repo, path, ref):
        self.calls.append(path)
        if path == ".env.example":
            return self.repos[repo].encode(), "sha"
        return None, None


@pytest.fixture
def env(tmp_path, monkeypatch):
    host = FixtureHost(server.build(tmp_path / "srv"))
    pem = host.real("/etc/mas/github-app.pem")
    pem.parent.mkdir(parents=True, exist_ok=True)
    pem.write_text("-----BEGIN PRIVATE KEY-----")
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password("correct horse battery"))
    config = Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key", github_app_id=1)
    gh = FakeGitHub()
    monkeypatch.setattr("mas.api.GitHubApp", lambda app_id, key: gh)
    ovh = FakeOvh()
    ovh.zones = ["indiepolis.com", "example.com"]
    save_providers(host, config, db, {"ovh": {"creds": CREDS, "account": "ab12345-ovh"}})
    db.set_setting(IPV4_KEY, IP)
    app = create_app(host, config, db, background=False, ovh_opener=ovh)
    deployer = app.state.deployer
    deployer._start_worker = lambda: None  # releases are queued, never built
    resolved: dict[str, list[str]] = {}
    deployer.resolver = lambda name: resolved.get(name, [])
    tokens = {}
    for scope in ("read", "write", "create"):
        t = auth.new_api_token()
        db.create_api_token(f"claude-{scope}", auth.hash_token(t), uid, scope)
        tokens[scope] = {"Authorization": f"Bearer {t}"}
    client = TestClient(app)
    csrf = {"X-CSRF-Token": client.post("/api/login", json={"username": "admin",
                                                            "password": "correct horse battery"}).json()["csrf"]}
    allow = client.put("/api/mcp", headers=csrf, json={"allowed_repos": ["SyanCode/*"]})
    assert allow.status_code == 200, allow.text
    agent = TestClient(app)  # no cookie: like the MCP endpoint
    return {"client": client, "csrf": csrf, "agent": agent, "tokens": tokens, "db": db, "gh": gh, "ovh": ovh,
            "host": host, "resolved": resolved}


def plan(e, **params):
    return e["agent"].get("/api/agent/plan", params={"repo": "SyanCode/Indiepolis", "branch": "main", **params},
                          headers=e["tokens"]["create"])


def create(e, **body):
    return e["agent"].post("/api/agent/apps", headers=e["tokens"]["create"],
                           json={"repo": "SyanCode/Indiepolis", "branch": "main", "app_id": "indiepolis", **body})


def no_value_in(text: str) -> None:
    for value in (SECRET_DEFAULT, "ssl0.ovh.net", "/srv/mas/apps/indiepolis/.home", "hello@indiepolis.com"):
        assert value not in text, value


def test_plan_reports_and_changes_nothing(env):
    db, ovh = env["db"], env["ovh"]
    before = (db.list_apps(), db.history(None, 100), dict(ovh.records))
    r = plan(env, app_id="indiepolis", domain="indiepolis.com")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["can_create"] and p["github"] == {"accessible": True, "installation_id": 42}
    assert p["type"] == "node" and "npm run build" in p["build"]
    assert set(p["missing_variables"]) == NAMES
    by_name = {v["name"]: v for v in p["variables"]}
    assert by_name["DB_PATH"]["has_default"] and not by_name["DB_PATH"]["required"]
    assert by_name["API_TOKEN"]["secret"] and not by_name["API_TOKEN"]["has_default"]
    assert by_name["STRIPE_SECRET_KEY"]["optional"] and "PORT" not in by_name
    assert p["domain"]["dns"] == "registrar" and p["domain"]["state"] == "free"
    no_value_in(r.text)
    assert (db.list_apps(), db.history(None, 100), dict(ovh.records)) == before


def test_create_waits_for_variables(env):
    db, ovh, agent, tokens = env["db"], env["ovh"], env["agent"], env["tokens"]
    r = create(env, domain="indiepolis.com")
    assert r.status_code == 200, r.text
    out = r.json()
    no_value_in(r.text)
    assert out["status"] == "waiting_for_variables" and out["release_id"] is None
    assert set(out["missing_variables"]) == NAMES and "Variables" in out["next"]
    assert out["dns"]["mode"] == "registrar" and any("A 203.0.113.7" in c for c in out["dns"]["changes"])
    assert {(r["subDomain"], r["fieldType"], r["target"]) for r in ovh.records.values()} == \
        {("", "A", IP), ("www", "A", IP)}
    assert db.list_releases("indiepolis", 5) == []

    # no build: deploy, rollback, push and deploy-all all wait
    d = agent.post("/api/apps/indiepolis/deploy", headers=tokens["write"])
    assert d.status_code == 409 and set(d.json()["pending_env"]) == NAMES
    assert "Apps → indiepolis → Variables" in d.json()["detail"]
    assert "indiepolis" not in [q["app_id"] for q in
                                env["client"].post("/api/apps/deploy-all", headers=env["csrf"]).json()["queued"]]
    assert db.list_releases("indiepolis", 5) == []
    assert agent.get("/api/apps/indiepolis", headers=tokens["read"]).json()["pending_env"]

    # the human fills two: still waiting; the last one starts the first deploy
    client, csrf = env["client"], env["csrf"]
    current = client.get("/api/apps/indiepolis/env").json()["text"]
    assert "DB_PATH=/srv/mas/apps/indiepolis/.home/data/indiepolis.db" in current
    assert "SMTP_HOST=ssl0.ovh.net" in current and SECRET_DEFAULT not in current
    partial = client.put("/api/apps/indiepolis/env", headers=csrf,
                         json={"text": current + "SIGNING_SECRET=s1\nADMIN_KEY=k1\n"}).json()
    assert set(partial["pending_env"]) == {"SMTP_PASS", "API_TOKEN"} and "release_id" not in partial
    done = client.put("/api/apps/indiepolis/env", headers=csrf, json={
        "text": current + "SIGNING_SECRET=s1\nADMIN_KEY=k1\nSMTP_PASS=p\nAPI_TOKEN=t\n"}).json()
    assert done["pending_env"] == [] and done["release_id"]
    assert db.list_releases("indiepolis", 5)[0]["trigger"] == "create"
    assert agent.post("/api/apps/indiepolis/deploy", headers=tokens["write"]).status_code == 200


def test_create_without_missing_variables_deploys(env):
    r = env["agent"].post("/api/agent/apps", headers=env["tokens"]["create"],
                          json={"repo": "SyanCode/plain", "branch": "main", "app_id": "plain"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deploying" and r.json()["release_id"]
    hist = env["db"].history(None, 5)
    assert hist[0]["action"] == "app-create" and hist[0]["actor"] == "admin (token claude-create)"


def test_manual_dns_gives_records_and_checks_them(env):
    agent, tokens = env["agent"], env["tokens"]
    r = env["agent"].post("/api/agent/apps", headers=tokens["create"], json={
        "repo": "SyanCode/plain", "branch": "main", "app_id": "plain", "domain": "plain.dev"})
    assert r.status_code == 200, r.text
    dns = r.json()["dns"]
    assert dns["mode"] == "manual"
    assert {"name": "@", "type": "A", "value": IP} in dns["records_to_create"]
    assert {"name": "www", "type": "CNAME", "value": "plain.dev."} in dns["records_to_create"]
    status = agent.get("/api/apps/plain/domains", headers=tokens["read"]).json()["domains"]
    assert status[0]["status"] == "missing"
    env["resolved"]["plain.dev"] = [IP]
    status = agent.get("/api/apps/plain/domains", headers=tokens["read"]).json()["domains"]
    assert status[0]["status"] == "ok"


def test_repository_must_be_allowed(env):
    assert plan(env, repo="Other/thing").status_code == 403
    assert create(env, repo="Other/thing").status_code == 403
    env["client"].put("/api/mcp", headers=env["csrf"], json={"allowed_repos": []})
    assert create(env).status_code == 403  # empty list: nothing may be created
    assert env["db"].list_apps() == []
    bad = env["client"].put("/api/mcp", headers=env["csrf"], json={"allowed_repos": ["../x"]})
    assert bad.status_code == 400


def test_tokens_cannot_widen_the_allowed_list(env):
    r = env["agent"].put("/api/mcp", headers=env["tokens"]["create"], json={"allowed_repos": ["Other/*"]})
    assert r.status_code == 403


def test_scopes(env):
    for scope in ("read", "write"):
        r = env["agent"].post("/api/agent/apps", headers=env["tokens"][scope],
                              json={"repo": "SyanCode/plain", "branch": "main", "app_id": "plain"})
        assert r.status_code == 403, scope
    assert env["client"].get("/api/agent/plan", params={"repo": "SyanCode/plain", "branch": "main"}).status_code == 403
    assert env["db"].list_apps() == []


def test_conflicts_are_refused(env):
    db, ovh = env["db"], env["ovh"]
    db.create_app("taken", name="taken", repo="SyanCode/x", branch="main", type="node", port=7150,
                  domain="taken.indiepolis.com")
    assert create(env, app_id="taken").status_code == 409
    assert create(env, domain="taken.indiepolis.com").status_code == 409
    assert "served by nginx" in create(env, domain="blog.example.com").json()["detail"]
    ovh.add("indiepolis.com", "old", "A", "198.51.100.9")
    r = create(env, domain="old.indiepolis.com")
    assert r.status_code == 409 and "never replaces" in r.json()["detail"]
    assert [r["target"] for r in ovh.records.values() if r["subDomain"] == "old"] == ["198.51.100.9"]
    ovh.add("indiepolis.com", "alias", "CNAME", "elsewhere.net.")
    assert create(env, domain="alias.indiepolis.com").status_code == 409
    assert create(env, repo="SyanCode/missing").status_code == 409  # GitHub App not installed there
    assert create(env, branch="nope").status_code == 409
    assert create(env, build_command="npm ci\nrm -rf /").status_code == 400  # one line, like the dashboard
    assert [a["id"] for a in db.list_apps()] == ["taken"]
    failed = [h for h in db.history(None, 50) if h["action"] == "app-create"]
    assert failed and not any(h["ok"] for h in failed)


def test_rate_limit(env):
    codes = [create(env, app_id=f"a{i}", repo="SyanCode/plain").status_code for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_plan_only_reads_text_files(env):
    plan(env)
    assert set(env["gh"].calls) <= {".env.example", ".env.sample"}
    assert json.dumps(plan(env).json()).count(SECRET_DEFAULT) == 0
