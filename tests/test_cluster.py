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


def test_traffic_per_backend(pair):
    a, ah, db, host = pair["a"], pair["ah"], pair["a_db"], pair["a_host"]
    db.add_cluster_server("b", "https://mas-b.example.com", "x", "10.0.0.2")
    db.create_app("web", name="web", repo="acme/web", branch="main", installation_id=7, domain="web.example.com",
                  type="nextjs", port=7100, mode="managed", cluster="[1]", cluster_backends='["10.0.0.2:7300"]')
    now = time.time()
    lines = [f"{now - 10:.3f} 127.0.0.1:7100 200 0.020", f"{now - 20:.3f} 10.0.0.2:7300 200 0.040",
             f"{now - 30:.3f} 10.0.0.2:7300 502 0.001", f"{now - 300:.3f} 127.0.0.1:7100 200 0.010",
             f"{now - 5:.3f} 10.0.0.2:7300, 127.0.0.1:7100 502, 200 0.300",  # retried here
             f"{now - 4000:.3f} 127.0.0.1:7100 200 0.010", "garbage"]
    host.write_text("/var/log/nginx/mas-app-web.upstream.log", "\n".join(lines) + "\n")
    r = a.get("/api/cluster/traffic").json()
    backends = {b["address"]: b for b in r["apps"][0]["backends"]}
    local, copy = backends["127.0.0.1:7100"], backends["10.0.0.2:7300"]
    assert local["server"] == "self" and local["requests_minute"] == 2 and local["requests_15min"] == 3
    assert copy["server"] == 1 and copy["requests_minute"] == 2 and copy["errors_15min"] == 1
    assert copy["avg_ms"] == 20 and local["configured"] and copy["configured"]
    stranger = TestClient(pair["a_app"])  # no session: a join token does not open the dashboard routes
    assert stranger.get("/api/cluster/traffic", headers={"Authorization": "Bearer masc_x"}).status_code == 401


def test_read_tail(tmp_path):
    from mas.host import Host
    (tmp_path / "log").write_bytes(b"first line\n" + b"x" * 50 + b"\nlast line\n")
    assert Host(tmp_path).read_tail("/log", 20) == b"last line\n"
    assert Host(tmp_path).read_tail("/log", 10_000).startswith(b"first line")


def test_traffic_follows_the_log(pair):
    app, db, host = pair["a_app"], pair["a_db"], pair["a_host"]
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100, mode="managed",
                  domain="web.example.com")
    cluster, now = app.state.cluster, time.time()
    log = "/var/log/nginx/mas-app-web.upstream.log"
    host.write_text(log, f"{now - 2:.3f} 127.0.0.1:7100 200 0.010\n")
    first = cluster.traffic(now)["apps"][0]["backends"][0]
    assert first["requests_minute"] == 1
    assert cluster.traffic(now + 1) is cluster.traffic(now + 1)  # cached for a few seconds
    reads = []
    original = host.read_from
    host.read_from = lambda p, o, n: reads.append(o) or original(p, o, n)
    with open(host.real(log), "a") as fh:
        fh.write(f"{now:.3f} 127.0.0.1:7100 200 0.010\n")
    later = cluster.traffic(now + 10)["apps"][0]["backends"][0]
    assert later["requests_minute"] == 2 and reads == [len(f"{now - 2:.3f} 127.0.0.1:7100 200 0.010\n")]


def test_an_agent_has_no_dashboard(tmp_path):
    from mas.agent import create_agent_app
    from mas.cluster import validate_url
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config(secret_key_path="/etc/mas/secret.key", cluster_role="agent", cluster_address="10.0.0.7")
    app = create_agent_app(host, config, db)
    client = TestClient(app)
    assert client.get("/api/health").json() == {"ok": True, "role": "agent"}
    for path in ("/api/services", "/api/apps", "/api/cluster", "/", "/api/session"):
        assert client.get(path).status_code == 404  # nothing but the calls of the primary server
    token = app.state.cluster.create_token("primary")
    info = client.get("/api/cluster/self", headers={"Authorization": f"Bearer {token}"}).json()
    assert info["address"] == "10.0.0.7"
    assert validate_url("http://10.0.0.7:7070") == "http://10.0.0.7:7070"
    for bad in ("http://203.0.113.4:7070", "http://mas.example.com", "ftp://10.0.0.7"):
        with pytest.raises(ClusterError):
            validate_url(bad)


def test_a_silent_server_gets_its_copies_moved(tmp_path):
    peers, down = {}, set()

    def route(method, url, headers, body, timeout):
        name = url.split("//")[1].split(".")[0]  # mas-b / mas-c
        if name in down:
            raise OSError("timed out")
        client = peers[name]
        r = client.request(method, "/" + url.split("/", 3)[3], headers=headers, content=body)
        return r.status_code, r.json()

    tokens = {}
    for name, addr in (("mas-b", "10.0.0.2"), ("mas-c", "10.0.0.3")):
        client, app, db, host, h = mas(tmp_path, name)
        app.state.deployer._start_worker = lambda: None
        client.put("/api/cluster/self", json={"name": name, "address": addr}, headers=h)
        tokens[name] = client.post("/api/cluster/tokens", json={"name": "a"}, headers=h).json()["token"]
        peers[name] = client
    a, a_app, db, a_host, ah = mas(tmp_path, "a", opener=route)
    a_app.state.deployer._start_worker = lambda: None
    ids = {n: a.post("/api/cluster/servers", json={"url": f"https://{n}.example.com", "token": tokens[n]},
                     headers=ah).json()["id"] for n in tokens}
    db.create_app("web", name="web", repo="acme/web", branch="main", installation_id=7, domain="web.example.com",
                  type="nextjs", port=7100, mode="managed", cluster=json.dumps([ids["mas-b"]]))
    cluster = a_app.state.cluster
    assert cluster.watch(now=1000) == []  # everybody answers
    down.add("mas-b")
    assert cluster.watch(now=1010) == [] and json.loads(db.get_app("web")["cluster"]) == [ids["mas-b"]]  # not yet
    notes = cluster.watch(now=1010 + 121)
    assert "copy moved (mas-b → mas-c)" in notes[0]
    assert json.loads(db.get_app("web")["cluster"]) == [ids["mas-c"]]
    assert db.list_releases("web", 1)[0]["trigger"] == "failover"  # the redeploy builds the copy on mas-c
    assert a.get("/api/cluster").json()["servers"][0]["down_for_s"] is not None
    # mas-b comes back with its old copy: removed there
    peers["mas-b"].app.state.cluster.db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs",
                                                   port=7300, mode="managed", replica_of=cluster.name(),
                                                   bind="10.0.0.2")
    down.clear()
    notes = cluster.watch(now=2000)
    assert any("old copy removed from mas-b" in n for n in notes)
    assert peers["mas-b"].app.state.cluster.db.get_app("web") is None


def test_a_server_takes_over_when_the_primary_is_gone(pair):
    a, b, ah, bh = pair["a"], pair["b"], pair["ah"], pair["bh"]
    a_app, b_app, a_db, b_db, a_host = pair["a_app"], pair["b_app"], pair["a_db"], pair["b_db"], pair["a_host"]
    b.put("/api/cluster/self", json={"name": "b", "address": "10.0.0.2"}, headers=bh)
    a.put("/api/cluster/self", json={"name": "a", "address": "10.0.0.1"}, headers=ah)
    token = b.post("/api/cluster/tokens", json={"name": "a"}, headers=bh).json()["token"]
    sid = a.post("/api/cluster/servers", json={"url": "https://mas-b.example.com", "token": token},
                 headers=ah).json()["id"]
    fernet = load_fernet(a_host, "/etc/mas/secret.key")
    a_db.create_app("web", name="web", repo="acme/web", branch="main", installation_id=7, domain="web.example.com",
                    type="nextjs", port=7100, mode="managed", aliases='["www.web.example.com"]',
                    env=encrypt_env(fernet, {"API_KEY": "s3cr3t"}), cluster=json.dumps([sid]))
    a_db.create_app("blog", name="blog", repo="acme/blog", branch="main", installation_id=7, domain="blog.example.com",
                    type="static", mode="managed")
    a_app.state.cluster.config.github_app_id = 42
    a_host.write_text(a_app.state.cluster.config.github_private_key_path, "-----BEGIN PRIVATE KEY-----\nx\n")
    # B runs a copy of web
    b_db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7300, mode="managed",
                    replica_of="a", bind="10.0.0.2")

    a_app.state.cluster.watch()  # sends its state
    state = b_app.state.cluster.stored_state()
    assert state["primary"]["name"] == "a" and {x["id"] for x in state["apps"]} == {"web", "blog"}
    assert "s3cr3t" not in (b_db.get_setting("cluster_state") or "")  # encrypted on B

    with pytest.raises(ClusterError):  # A still answers: refused without --force
        b_app.state.cluster.promote(probe=lambda address: True)
    done = b_app.state.cluster.promote(probe=lambda address: False)
    assert done["epoch"] == 2 and set(done["apps"]) == {"web", "blog"}
    assert done["domains"] == ["blog.example.com", "web.example.com", "www.web.example.com"]
    web = b_db.get_app("web")
    assert web["replica_of"] is None and web["bind"] is None and web["domain"] == "web.example.com"
    assert web["port"] == 7300 and b_db.get_app("blog")["type"] == "static"  # the copy became the app
    assert b_db.get_user("admin") is not None
    assert "app_id = 42" in (pair["b_app"].state.cluster.host.read_text("/etc/mas/config.toml") or "")
    assert 'role = "primary"' in pair["b_app"].state.cluster.host.read_text("/etc/mas/config.toml")
    assert set(json.loads(b_db.get_setting("cluster_promoted_deploys"))) == {"web", "blog"}

    # the former primary comes back: B refuses its calls
    r = b.get("/api/cluster/self", headers={"Authorization": f"Bearer {token}", "X-Mas-Primary": "1:a"})
    assert r.status_code == 409 and "not the primary anymore" in r.json()["detail"]
