"""Two Mas servers: A drives, B runs a copy of an app of A."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.cluster import ClusterError, validate_address
from mas.config import Config
from mas.db import Database
from mas.deploy import ReleaseLog
from mas.envcrypt import encrypt_env, load_fernet
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
SHA = "a" * 40


class FakeGitHub:
    def installation_for(self, repo):
        return 7

    def token(self, installation_id):
        return "ghs_clone_token_for_b"

    def commit(self, installation_id, repo, ref):
        return {"sha": ref, "message": "new version", "author": "Alice"}


def mas(tmp_path: Path, name: str, opener=None):
    host = FixtureHost(server.build(tmp_path / name / "srv"))
    db = Database(tmp_path / name / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    config = Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key")
    app = create_app(host, config, db, background=False, cluster_opener=opener)
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]
    return client, app, db, host, {"X-CSRF-Token": csrf}


@pytest.fixture
def pair(tmp_path):
    b, b_app, b_db, b_host, bh = mas(tmp_path, "b")
    b_app.state.deployer._start_worker = lambda: None  # B's copies are checked, not built
    sent: list = []

    def to_b(method, url, headers, body, timeout):
        assert url.startswith("https://mas-b.example.com/")
        sent.append((method, url, json.loads(body) if body else None))
        r = b.request(method, url.removeprefix("https://mas-b.example.com"), headers=headers, content=body)
        return r.status_code, r.json()

    a, a_app, a_db, a_host, ah = mas(tmp_path, "a", opener=to_b)
    a_app.state.deployer.github = lambda: FakeGitHub()
    a_app.state.deployer._start_worker = lambda: None
    return {"a": a, "a_app": a_app, "a_db": a_db, "ah": ah, "b": b, "b_app": b_app, "b_db": b_db, "bh": bh,
            "a_host": a_host, "sent": sent}


def test_private_addresses_only():
    assert validate_address("10.0.0.2") == "10.0.0.2" and validate_address("100.101.1.2") == "100.101.1.2"
    with pytest.raises(ClusterError):
        validate_address("203.0.113.9")


def test_join_overview_and_a_copy(pair):
    a, b, ah, bh = pair["a"], pair["b"], pair["ah"], pair["bh"]
    assert b.put("/api/cluster/self", json={"name": "b", "address": "203.0.113.9"}, headers=bh).status_code == 400
    assert b.put("/api/cluster/self", json={"name": "b", "address": "10.0.0.2"}, headers=bh).status_code == 200
    token = b.post("/api/cluster/tokens", json={"name": "from a"}, headers=bh).json()["token"]
    assert b.get("/api/cluster/self", headers={"Authorization": "Bearer masc_wrong"}).status_code == 401
    assert b.get("/api/cluster/self").status_code == 401  # no session nor token

    assert a.post("/api/cluster/servers", json={"url": "https://mas-b.example.com", "token": "masc_bad_token"},
                  headers=ah).status_code == 400
    added = a.post("/api/cluster/servers", json={"url": "https://mas-b.example.com/", "token": token}, headers=ah)
    assert added.status_code == 200 and added.json()["address"] == "10.0.0.2"
    view = a.get("/api/cluster").json()
    assert view["servers"][0]["info"]["name"] == "b" and view["servers"][0]["error"] is None
    assert token not in json.dumps(view) and token not in json.dumps(pair["a_db"].list_cluster_servers())

    # an app of A on B too
    db = pair["a_db"]
    fernet = load_fernet(pair["a_host"], "/etc/mas/secret.key")
    db.create_app("web", name="web", repo="acme/web", branch="main", installation_id=7, domain="web.example.com",
                  type="nextjs", port=7100, mode="managed", env=encrypt_env(fernet, {"API_KEY": "s3cr3t"}))
    r = a.put("/api/apps/web/cluster", json={"servers": [added.json()["id"]]}, headers=ah)
    assert r.status_code == 200, r.text
    log = ReleaseLog(Path(pair["a_db"].path).parent / "x.log")
    wait = pair["a_app"].state.deployer.spread(db.get_app("web"), SHA, log)
    for _ in range(100):  # the copies are asked in the background
        put = next((body for method, url, body in pair["sent"] if method == "PUT"), None)
        if put:
            break
        time.sleep(0.05)
    assert put["git_token"] == "ghs_clone_token_for_b" and put["spec"]["env"] == {"API_KEY": "s3cr3t"}
    copy = pair["b_db"].get_app("web")
    assert copy["bind"] == "10.0.0.2" and copy["replica_of"] and copy["domain"] is None and not copy["auto_deploy"]
    rid = pair["b_db"].list_releases("web", 1)[0]["id"]
    assert pair["b_app"].state.deployer.sources[rid].token(0) == "ghs_clone_token_for_b"
    pair["b_db"].update_release(rid, status="live")  # B built it
    assert wait() == [f"10.0.0.2:{copy['port']}"]

    # B refuses to overwrite one of its own apps, and to delete it
    pair["b_db"].create_app("own", name="own", repo="acme/own", branch="main", type="node", port=7200)
    hdr = {"Authorization": f"Bearer {token}"}
    body = {**put, "spec": {**put["spec"]}}
    assert b.put("/api/cluster/copies/own", json=body, headers=hdr).status_code == 400
    assert b.delete("/api/cluster/copies/own", headers=hdr).status_code == 400

    # leaving: the copy goes away on B
    assert a.delete(f"/api/cluster/servers/{added.json()['id']}", headers=ah).status_code == 200
    assert pair["b_db"].get_app("web") is None and json.loads(db.get_app("web")["cluster"] or "[]") == []
