"""Known vulnerabilities in the packages of the apps (OSV), as health checks and in the API."""

from __future__ import annotations

import json
import time

import server
from fastapi.testclient import TestClient

from mas import auth, depaudit, templates
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.depaudit import OSV, DepAudit, describe_vuln, parse_package_lock, parse_requirements, version_key
from mas.health import run_checks
from mas.host import FixtureHost

NEXT_ADVISORY = {
    "id": "GHSA-mfr8-8qww-v76h", "summary": "Next.js: information disclosure across root params",
    "aliases": ["CVE-2026-0001"], "database_specific": {"severity": "MODERATE"},
    "affected": [{"package": {"name": "next", "ecosystem": "npm"},
                  "ranges": [{"type": "SEMVER", "events": [{"introduced": "16.3.0"}, {"fixed": "16.4.5"}]}]}],
}
LODASH_ADVISORY = {
    "id": "GHSA-lodash", "summary": "Prototype pollution", "database_specific": {"severity": "HIGH"},
    "affected": [{"package": {"name": "lodash", "ecosystem": "npm"},
                  "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.17.21"}]}]}],
}
LOCK = {
    "name": "site", "lockfileVersion": 3,
    "packages": {
        "": {"name": "site", "dependencies": {"next": "^16.4.0"}},
        "node_modules/next": {"version": "16.4.2"},
        "node_modules/lodash": {"version": "4.17.20"},
        "node_modules/react": {"version": "19.2.0"},
        "node_modules/eslint": {"version": "9.0.0", "dev": True},
    },
}


class FakeOSV:
    def __init__(self, vulns: dict[str, list[str]] | None = None, fail: bool = False) -> None:
        self.vulns = vulns if vulns is not None else {"next@16.4.2": ["GHSA-mfr8-8qww-v76h"], "lodash@4.17.20": ["GHSA-lodash"]}
        self.fail = fail
        self.batches: list[list[dict]] = []

    def __call__(self, url: str, body: dict | None) -> dict:
        if self.fail:
            raise depaudit.DepAuditError("OSV unreachable: timed out")
        if url == depaudit.OSV_BATCH:
            self.batches.append(body["queries"])
            return {"results": [{"vulns": [{"id": i} for i in self.vulns.get(
                f"{q['package']['name']}@{q['version']}", [])]} for q in body["queries"]]}
        vid = url.rsplit("/", 1)[-1]
        return {"GHSA-mfr8-8qww-v76h": NEXT_ADVISORY, "GHSA-lodash": LODASH_ADVISORY}[vid]


def make_app(host: FixtureHost, db: Database, app_id: str = "site", lock: dict | None = None) -> None:
    db.create_app(app_id, name=app_id, repo="me/site", branch="main", domain=f"{app_id}.example", type="nextjs",
                  mode="managed")
    rid = db.create_release(app_id, "push", "me", "abc", status="live")
    db.update_release(rid, finished_at="2020-01-01T00:00:00+00:00")
    folder = host.real(f"/srv/mas/apps/{app_id}/current")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "package-lock.json").write_text(json.dumps(lock or LOCK))
    (folder / "package.json").write_text(json.dumps({"dependencies": {"next": "^16.4.0"},
                                                     "devDependencies": {"eslint": "9"}}))


def setup(tmp_path, fake: FakeOSV | None = None):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    fake = fake or FakeOSV()
    return host, db, DepAudit(db, host, Config(), OSV(fake)), fake


# -- parsing ------------------------------------------------------------------------------------------


def test_lock_skips_dev_packages_and_marks_direct_ones():
    pkgs = {p["name"]: p for p in parse_package_lock(json.dumps(LOCK), {"next"})}
    assert set(pkgs) == {"next", "lodash", "react"}
    assert pkgs["next"]["direct"] and not pkgs["lodash"]["direct"]


def test_requirements_only_exact_pins():
    pkgs = parse_requirements("Django==5.0.1\nrequests>=2\n# x==1\nPillow[extra] == 10.0.0 ; python_version>'3'\n")
    assert [(p["name"], p["version"]) for p in pkgs] == [("django", "5.0.1"), ("pillow", "10.0.0")]


def test_version_order_and_ranges():
    assert version_key("16.4.2") < version_key("16.4.5") < version_key("16.10.0")
    assert version_key("16.5.0-canary.1") < version_key("16.5.0")
    pkg = {"ecosystem": "npm", "name": "next", "version": "16.4.2"}
    v = describe_vuln(NEXT_ADVISORY, pkg)
    assert v["affected"] and v["fixed"] == "16.4.5" and v["severity"] == "moderate" and v["cve"] == ["CVE-2026-0001"]
    assert not describe_vuln(NEXT_ADVISORY, pkg | {"version": "16.4.6"})["affected"]
    assert not describe_vuln(NEXT_ADVISORY, pkg | {"version": "16.2.0"})["affected"]


# -- the audit ----------------------------------------------------------------------------------------


def test_run_finds_vulnerable_packages_and_the_upgrade(tmp_path):
    host, db, audit, fake = setup(tmp_path)
    make_app(host, db)
    [res] = audit.run()
    assert res["packages"] == 3 and res["lockfiles"] == ["package-lock.json"] and res["error"] is None
    assert [(f["name"], f["direct"], f["upgrade_to"]) for f in res["findings"]] == [
        ("next", True, "16.4.5"), ("lodash", False, "4.17.21")]
    sent = {q["package"]["name"] for q in fake.batches[0]}
    assert "eslint" not in sent  # dev packages never leave the server


def test_health_checks_error_for_direct_dependency(tmp_path):
    host, db, audit, _ = setup(tmp_path)
    make_app(host, db)
    audit.run()
    checks = [c for c in run_checks(host, Config(), dns=False, db=db) if c.category == "dependencies"]
    by_pkg = {c.title.split(": ")[1].split(" ")[0]: c for c in checks}
    assert by_pkg["next"].level == "error" and "16.4.5" in by_pkg["next"].fix and by_pkg["next"].target == "site"
    assert by_pkg["lodash"].level == "error"  # high severity, even as a sub-dependency
    audit.configure(False)
    assert not [c for c in run_checks(host, Config(), dns=False, db=db) if c.category == "dependencies"]


def test_osv_down_keeps_the_last_result(tmp_path):
    host, db, audit, fake = setup(tmp_path)
    make_app(host, db)
    audit.run()
    fake.fail = True
    [res] = audit.run()
    assert res["error"].startswith("OSV unreachable") and len(res["findings"]) == 2


def test_tick_daily_and_after_a_new_release(tmp_path):
    host, db, audit, fake = setup(tmp_path)
    make_app(host, db)
    now = time.time()
    assert audit.tick(now) == ["site"]
    assert audit.tick(now + 3600) == []  # checked an hour ago, same release
    rid = db.create_release("site", "push", "me", "def", status="live")
    db.update_release(rid, finished_at="2020-01-02T00:00:00+00:00")
    assert audit.tick(now + 3600) == ["site"]
    assert audit.tick(now + 2 * 3600) == []
    assert audit.tick(now + 3600 + depaudit.DAY) == ["site"]
    audit.configure(False)
    assert audit.tick(now + 10 * depaudit.DAY) == []


def test_clean_app_no_check(tmp_path):
    host, db, audit, _ = setup(tmp_path, FakeOSV({}))
    make_app(host, db)
    audit.run()
    assert not [c for c in run_checks(host, Config(), dns=False, db=db) if c.category == "dependencies"]


def test_deleting_the_app_forgets_its_result(tmp_path):
    host, db, audit, _ = setup(tmp_path)
    make_app(host, db)
    audit.run()
    db.delete_app("site")
    assert audit.latest("site") is None


# -- fingerprints ------------------------------------------------------------------------------------


def test_vhosts_hide_software_versions():
    for https in (True, False):
        conf = templates.vhost("site", "site.example", templates.proxy_body(7100, "/var/lib/mas/pages"), https)
        assert "server_tokens off;" in conf and "proxy_hide_header X-Powered-By;" in conf


# -- API ----------------------------------------------------------------------------------------------


def api(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password("correct horse battery"))
    app = create_app(host, Config(cookie_secure=False), db, background=False, osv_fetch=FakeOSV())
    return TestClient(app), db, uid, host


def test_api_scan_and_settings(tmp_path):
    client, db, uid, host = api(tmp_path)
    make_app(host, db)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    h = {"X-CSRF-Token": csrf}
    assert client.get("/api/dependencies").json()["apps"][0]["at"] is None
    r = client.post("/api/dependencies/scan", headers=h)
    assert r.status_code == 200, r.text
    assert [f["name"] for f in r.json()["apps"][0]["findings"]] == ["next", "lodash"]
    r = client.put("/api/dependencies/settings", headers=h, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert client.post("/api/dependencies/scan?app_id=nope", headers=h).status_code == 404


def test_api_token_cannot_change_the_settings(tmp_path):
    client, db, uid, _ = api(tmp_path)
    token = auth.new_api_token()
    db.create_api_token("bot", auth.hash_token(token), uid, "write", None)
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/dependencies", headers=h).status_code == 200
    assert client.put("/api/dependencies/settings", headers=h, json={"enabled": False}).status_code == 403
    assert not auth.api_token_allowed("/api/dependencies/settings")
