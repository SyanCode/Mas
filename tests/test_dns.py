"""OVH DNS integration: signed client, record logic, API, wait for DNS before certbot."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.dns import IPV4_KEY, load_ovh_credentials, save_ovh
from mas.host import FixtureHost
from mas.ovh import (
    DnsConflict, OvhClient, OvhCredentials, OvhError, point_domain, signature, token_url, validate_ip,
)

IP = "93.184.216.34"
CREDS = OvhCredentials("ovh-eu", "AK", "AS-secret", "CK-secret")


class FakeOvh:
    """Just enough of api.ovh.com for /me, zones and records."""

    def __init__(self):
        self.zones = ["example.com", "sxn.fr"]
        self.records: dict[int, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.next_id = 1

    def add(self, zone, sub, ftype, target):
        rid = self.next_id
        self.next_id += 1
        self.records[rid] = {"id": rid, "zone": zone, "subDomain": sub, "fieldType": ftype, "target": target}
        return rid

    def __call__(self, method, url, headers, body):
        parsed = urlparse(url)
        path = parsed.path.removeprefix("/1.0")
        self.calls.append((method, path))
        if path == "/auth/time":
            return 200, 1_700_000_000
        assert headers["X-Ovh-Signature"].startswith("$1$")
        assert headers["X-Ovh-Consumer"] == "CK-secret"
        if path == "/me":
            return 200, {"nichandle": "ab12345-ovh", "firstname": "Syan", "name": None}
        if path == "/domain/zone":
            return 200, self.zones
        parts = path.split("/")  # ['', 'domain', 'zone', zone, 'record', id?]
        zone = parts[3]
        if zone not in self.zones:
            return 404, {"message": "This service does not exist"}
        if parts[4:] == ["refresh"]:
            return 200, None
        if parts[4:] == ["record"] and method == "GET":
            q = parse_qs(parsed.query, keep_blank_values=True)
            return 200, [r["id"] for r in self.records.values() if r["zone"] == zone
                         and r["fieldType"] == q["fieldType"][0] and r["subDomain"] == q["subDomain"][0]]
        if parts[4:] == ["record"] and method == "POST":
            data = json.loads(body)
            return 200, self.records[self.add(zone, data["subDomain"], data["fieldType"], data["target"])]
        rid = int(parts[5])
        if method == "GET":
            return 200, self.records[rid]
        if method == "PUT":
            self.records[rid]["target"] = json.loads(body)["target"]
            return 200, None
        return 400, {"message": f"unexpected {method} {path}"}


def test_signature_and_token_url():
    sig = signature("AS", "CK", "GET", "https://eu.api.ovh.com/1.0/me", "", 1_700_000_000)
    assert sig == "$1$" + hashlib.sha1(b"AS+CK+GET+https://eu.api.ovh.com/1.0/me++1700000000").hexdigest()
    url = token_url("ovh-eu")
    assert url.startswith("https://eu.api.ovh.com/createToken/?GET=/me")
    assert "DELETE" not in url


def test_point_domain_creates_and_is_idempotent():
    fake = FakeOvh()
    client = OvhClient(CREDS, fake)
    result = point_domain(client, "example.com", "Test", IP, "2001:db8::1")
    assert result["domain"] == "test.example.com"
    assert {(r["fieldType"], r["subDomain"], r["target"]) for r in fake.records.values()} == {
        ("A", "test", IP), ("AAAA", "test", "2001:db8::1")}
    assert ("POST", "/domain/zone/example.com/refresh") in fake.calls

    fake.calls.clear()
    again = point_domain(client, "example.com", "test", IP, "2001:db8::1")
    assert all("already set" in c for c in again["changes"])
    assert ("POST", "/domain/zone/example.com/refresh") not in fake.calls


def test_point_domain_conflicts():
    fake = FakeOvh()
    client = OvhClient(CREDS, fake)
    rid = fake.add("example.com", "shop", "A", "1.2.3.4")
    with pytest.raises(DnsConflict) as exc:
        point_domain(client, "example.com", "shop", IP)
    assert exc.value.kind == "points-elsewhere" and "1.2.3.4" in str(exc.value)
    point_domain(client, "example.com", "shop", IP, overwrite=True)
    assert fake.records[rid]["target"] == IP and len(fake.records) == 1

    fake.add("example.com", "www", "CNAME", "example.com.")
    with pytest.raises(DnsConflict) as exc:
        point_domain(client, "example.com", "www", IP, overwrite=True)
    assert exc.value.kind == "cname"

    with pytest.raises(OvhError):
        point_domain(client, "not-mine.com", "x", IP)
    with pytest.raises(OvhError):
        point_domain(client, "example.com", "bad_label!", IP)


def test_validate_ip():
    assert validate_ip(IP, 4) == IP
    for bad, version in [("192.168.1.10", 4), ("2001:db8::1", 4), ("nope", 4), ("127.0.0.1", 4)]:
        with pytest.raises(OvhError):
            validate_ip(bad, version)


def test_credentials_are_encrypted_at_rest(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config(secret_key_path="/etc/mas/secret.key")
    save_ovh(host, config, db, CREDS)
    raw = db.get_setting("ovh_credentials")
    assert "AS-secret" not in raw and "CK-secret" not in raw
    assert load_ovh_credentials(host, config, db) == CREDS


# -- API -----------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands["*"] = {"returncode": 0}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    fake = FakeOvh()
    app = create_app(host, Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key"), db,
                     background=False, ovh_opener=fake)
    queued: list = []
    app.state.deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: \
        queued.append((app_id, trigger)) or 7
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100)
    return client, db, fake, queued, {"X-CSRF-Token": csrf}


def connect(client, h):
    body = {"endpoint": "ovh-eu", "application_key": "AK", "application_secret": "AS-secret",
            "consumer_key": "CK-secret"}
    return client.post("/api/dns/ovh", json=body, headers=h)


def test_connect_and_settings(api):
    client, db, fake, queued, h = api
    r = connect(client, h)
    assert r.status_code == 200 and r.json()["zones"] == ["example.com", "sxn.fr"]
    status = client.get("/api/dns/status").json()
    assert status["provider"] == "ovh" and status["account"]["nichandle"] == "ab12345-ovh"
    assert client.put("/api/dns/settings", json={"ipv4": "192.168.1.10"}, headers=h).status_code == 400
    assert client.put("/api/dns/settings", json={"ipv4": IP}, headers=h).json()["ipv4"] == IP
    assert client.get("/api/dns/zones").json() == ["example.com", "sxn.fr"]


def test_link_domain_creates_record_and_redeploys(api):
    client, db, fake, queued, h = api
    connect(client, h)
    body = {"zone": "example.com", "subdomain": "web"}
    assert "IPv4" in client.post("/api/apps/web/domain", json=body, headers=h).json()["detail"]
    db.set_setting(IPV4_KEY, IP)

    check = client.get("/api/dns/check?zone=example.com&subdomain=web").json()
    assert check["domain"] == "web.example.com" and not check["records"] and not check["used_by"]

    r = client.post("/api/apps/web/domain", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["domain"] == "web.example.com" and r.json()["release_id"] == 7
    assert db.get_app("web")["domain"] == "web.example.com"
    assert queued == [("web", "manual")]
    assert any(rec["target"] == IP and rec["subDomain"] == "web" for rec in fake.records.values())
    assert client.get("/api/dns/check?zone=example.com&subdomain=web").json()["points_here"]


def test_link_domain_refusals(api):
    client, db, fake, queued, h = api
    connect(client, h)
    db.set_setting(IPV4_KEY, IP)
    fake.add("example.com", "taken", "A", "9.9.9.9")
    r = client.post("/api/apps/web/domain", json={"zone": "example.com", "subdomain": "taken"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"].startswith("points-elsewhere:")
    assert db.get_app("web")["domain"] is None and queued == []
    r = client.post("/api/apps/web/domain", json={"zone": "example.com", "subdomain": "taken", "overwrite": True},
                    headers=h)
    assert r.status_code == 200

    # already served by a vhost of the fixture server
    r = client.post("/api/apps/web/domain", json={"domain": "blog.example.com"}, headers=h)
    assert r.status_code == 409 and "already served" in r.json()["detail"]

    db.create_app("legacy", name="l", repo="acme/l", branch="main", type="php", mode="adopted")
    r = client.post("/api/apps/legacy/domain", json={"domain": "x.example.com"}, headers=h)
    assert r.status_code == 400


def test_deploy_waits_for_dns_before_certbot(tmp_path, monkeypatch):
    from mas.deploy import Deployer, ReleaseLog

    monkeypatch.setattr("mas.deploy.time.sleep", lambda s: None)
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.set_setting(IPV4_KEY, IP)
    answers = [[], ["1.1.1.1"], [IP]]
    deployer = Deployer(host, Config(dns_wait_seconds=30), db, lambda: None, resolver=lambda name: answers.pop(0))
    log = ReleaseLog(tmp_path / "x.log")
    deployer._wait_dns("web.example.com", log)
    log.close()
    assert f"DNS: web.example.com → {IP}" in (tmp_path / "x.log").read_text(encoding="utf-8")
    assert answers == []
