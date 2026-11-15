"""File sharing: upload, public link, password, limits, expiry, and what an API token or a viewer cannot do."""

from __future__ import annotations

import time

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth, shares
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
DATA = bytes(range(256)) * 400  # 102400 bytes


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "data" / "mas.db")
    uid = db.create_user("admin", auth.hash_password(PASSWORD))
    client = TestClient(create_app(host, Config(cookie_secure=False), db, background=False))
    session = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()
    admin = {"X-CSRF-Token": session["csrf"]}
    return client, db, uid, admin


def share(client, admin, data=DATA, name="big.bin", **options):
    r = client.post("/api/shares", headers=admin, json={"name": name, "size": len(data), **options})
    assert r.status_code == 200, r.text
    item = r.json()
    r = client.put(f"/api/shares/{item['id']}/content", headers=admin, content=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_then_anyone_downloads(env):
    client, db, _, admin = env
    item = share(client, admin)
    assert item["ready"] and item["url_path"] == f"/d/{item['id']}" and not item["has_password"]
    anonymous = TestClient(client.app)  # no cookie: a visitor
    page = anonymous.get(item["url_path"])
    assert page.status_code == 200 and "big.bin" in page.text and "<script" not in page.text
    r = anonymous.get(f"{item['url_path']}/file")
    assert r.status_code == 200 and r.content == DATA
    assert 'attachment; filename="big.bin"' in r.headers["content-disposition"]
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert shares.get(db, item["id"])["downloads"] == 1
    assert (shares.folder(db) / item["id"]).stat().st_mode & 0o077 == 0


def test_resume_with_a_range_keeps_the_count(env):
    client, db, _, admin = env
    item = share(client, admin)
    anonymous = TestClient(client.app)
    r = anonymous.get(f"{item['url_path']}/file", headers={"Range": "bytes=1000-1999"})
    assert r.status_code == 206 and r.content == DATA[1000:2000]
    assert shares.get(db, item["id"])["downloads"] == 0


def test_password(env):
    client, db, _, admin = env
    item = share(client, admin, password="s3cret!")
    assert item["has_password"] and "s3cret" not in str(shares.listing(db))
    anonymous = TestClient(client.app)
    url = item["url_path"]
    assert "type=\"password\"" in anonymous.get(url).text
    assert anonymous.get(f"{url}/file").status_code == 401
    assert anonymous.get(f"{url}/file", params={"t": "1.forged"}).status_code == 401
    bad = anonymous.post(url, data={"password": "nope"})
    assert bad.status_code == 403 and "Wrong password" in bad.text and "?t=" not in bad.text
    good = anonymous.post(url, data={"password": "s3cret!"})
    assert good.status_code == 200 and "?t=" in good.text
    link = good.text.split('href="')[1].split('"')[0].replace("&amp;", "&")
    assert anonymous.get(link).content == DATA
    # curl -u :password
    assert anonymous.get(f"{url}/file", auth=("", "s3cret!")).content == DATA
    assert anonymous.get(f"{url}/file", auth=("", "wrong")).status_code == 401


def test_changing_the_password_revokes_signed_links(env):
    client, db, _, admin = env
    item = share(client, admin, password="first-pass")
    anonymous = TestClient(client.app)
    page = anonymous.post(item["url_path"], data={"password": "first-pass"}).text
    link = page.split('href="')[1].split('"')[0].replace("&amp;", "&")
    assert anonymous.get(link).status_code == 200
    r = client.patch(f"/api/shares/{item['id']}", headers=admin, json={"password": "second-pass"})
    assert r.status_code == 200
    assert anonymous.get(link).status_code == 401
    assert anonymous.get(item["url_path"] + "/file", auth=("", "second-pass")).status_code == 200


def test_wrong_passwords_are_rate_limited(env):
    client, db, _, admin = env
    item = share(client, admin, password="s3cret!")
    anonymous = TestClient(client.app)
    codes = [anonymous.post(item["url_path"], data={"password": f"bad{i}"}).status_code for i in range(10)]
    assert codes[:8] == [403] * 8 and codes[8:] == [429, 429]
    assert anonymous.post(item["url_path"], data={"password": "s3cret!"}).status_code == 429


def test_download_limit_and_expiry(env):
    client, db, _, admin = env
    item = share(client, admin, max_downloads=2)
    anonymous = TestClient(client.app)
    assert [anonymous.get(f"{item['url_path']}/file").status_code for _ in range(3)] == [200, 200, 404]
    assert anonymous.get(item["url_path"]).status_code == 404
    other = share(client, admin, expires_hours=1)
    with db.tx() as c:
        c.execute("UPDATE shares SET expires_at = ? WHERE id = ?", (time.time() - 5, other["id"]))
    assert anonymous.get(f"{other['url_path']}/file").status_code == 404
    assert shares.sweep(db) == 2
    assert not list((shares.folder(db)).iterdir()) and shares.listing(db) == []


def test_upload_must_match_the_announced_size(env):
    client, db, _, admin = env
    item = client.post("/api/shares", headers=admin, json={"name": "a", "size": 100}).json()
    r = client.put(f"/api/shares/{item['id']}/content", headers=admin, content=b"x" * 99)
    assert r.status_code == 400
    r = client.put(f"/api/shares/{item['id']}/content", headers=admin, content=b"x" * 101)
    assert r.status_code == 413
    assert list((shares.folder(db)).iterdir()) == []
    assert TestClient(client.app).get(f"{item['url_path']}/file").status_code == 404  # not ready: not public
    assert client.put(f"/api/shares/{item['id']}/content", headers=admin, content=b"x" * 100).status_code == 200
    assert client.put(f"/api/shares/{item['id']}/content", headers=admin, content=b"x" * 100).status_code == 409


def test_not_enough_disk_space(env, monkeypatch):
    client, _, _, admin = env
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(shares.shutil, "disk_usage", lambda _p: usage(10, 10, shares.RESERVE + 50))
    r = client.post("/api/shares", headers=admin, json={"name": "a", "size": 100})
    assert r.status_code == 400 and "disk" in r.json()["detail"]


def test_names_cannot_escape_or_render(env):
    client, db, _, admin = env
    assert shares.clean_name("../../etc/passwd") == "passwd"
    assert shares.clean_name('..\\x"y\r\n.html') == "xy.html"
    item = share(client, admin, name="<script>alert(1)</script>.html")
    anonymous = TestClient(client.app)
    assert "<script>alert" not in anonymous.get(item["url_path"]).text
    assert anonymous.get(f"{item['url_path']}/file").headers["content-type"] == "application/octet-stream"
    assert anonymous.get("/d/..%2f..%2fetc%2fpasswd").status_code in (404,)
    assert anonymous.get("/d/short/file").status_code == 404


def test_delete_removes_the_file(env):
    client, db, _, admin = env
    item = share(client, admin)
    assert client.delete(f"/api/shares/{item['id']}", headers=admin).status_code == 200
    assert not (shares.folder(db) / item["id"]).exists()
    assert client.delete(f"/api/shares/{item['id']}", headers=admin).status_code == 404


def test_admin_only_and_never_with_an_api_token_or_a_viewer(env):
    client, db, uid, admin = env
    item = share(client, admin)
    token = auth.new_api_token()
    db.create_api_token("agent", auth.hash_token(token), uid, "write", None)
    bearer = {"Authorization": f"Bearer {token}"}
    anonymous = TestClient(client.app)
    for c, headers in ((anonymous, {}), (anonymous, bearer), (TestClient(client.app), bearer)):
        assert c.get("/api/shares", headers=headers).status_code in (401, 403)
    assert not auth.api_token_allowed("/api/shares") and not auth.api_token_allowed(f"/api/shares/{item['id']}/content")
    db.create_user("viewer", auth.hash_password(PASSWORD), role="viewer")
    other = TestClient(client.app)
    other.post("/api/login", json={"username": "viewer", "password": PASSWORD})
    assert other.get("/api/shares").status_code == 403
