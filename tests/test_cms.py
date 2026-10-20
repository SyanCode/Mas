"""CMS: schema validation, value checks, commits, editor accounts and access control."""

from __future__ import annotations

import json

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.cms import CmsError, apply_values, content_candidates, detect_image, infer_schema, parse_schema
from mas.config import Config
from mas.db import Database
from mas.github import GitHubError
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

SCHEMA = {
    "pages": [{
        "id": "home", "label": "Home", "file": "content/home.json",
        "fields": [
            {"key": "title", "type": "text", "max": 20, "required": True},
            {"key": "cta", "type": "url"},
            {"key": "theme", "type": "select", "options": ["light", "dark"]},
            {"key": "hero", "type": "image"},
            {"key": "services", "type": "list", "max_items": 3,
             "fields": [{"key": "name", "type": "text", "required": True}, {"key": "price", "type": "number"}]},
        ],
    }],
}


class FakeGitHub:
    """Repository in memory: {path: bytes}, one branch."""

    def __init__(self, files: dict[str, bytes], write: bool = True) -> None:
        self.files = dict(files)
        self.sha = "c0"
        self.commits: list[tuple[str, dict[str, bytes]]] = []
        self.write = write

    def permissions(self, installation_id):
        return {"contents": "write" if self.write else "read"}

    def head(self, installation_id, repo, branch):
        return self.sha

    def tree(self, installation_id, repo, ref):
        return [{"path": p, "size": len(d)} for p, d in self.files.items()]

    def read_file(self, installation_id, repo, path, ref):
        data = self.files.get(path)
        return (data, f"blob-{hash(data) & 0xffff}") if data is not None else (None, None)

    def commit_files(self, installation_id, repo, branch, files, message, parent):
        if not self.write:
            raise GitHubError("GitHub API POST /git/blobs: HTTP 403 Resource not accessible by integration")
        if parent != self.sha:
            raise GitHubError("GitHub API PATCH refs: HTTP 422 Update is not a fast forward")
        self.files.update(files)
        self.sha = f"c{len(self.commits) + 1}"
        self.commits.append((message, files))
        return self.sha


def no_upload(token):
    raise CmsError("no upload")


def test_schema_validation():
    schema = parse_schema(json.dumps(SCHEMA))
    hero = schema["pages"][0]["fields"][3]
    assert hero["dir"] == "public/uploads" and hero["url"] == "/uploads"
    bad = [
        {"pages": []},
        {"pages": [{"id": "x", "file": "../etc/passwd.json", "fields": [{"key": "a"}]}]},
        {"pages": [{"id": "x", "file": ".github/workflows/ci.json", "fields": [{"key": "a"}]}]},
        {"pages": [{"id": "x", "file": "a.yml", "fields": [{"key": "a"}]}]},
        {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a", "type": "html"}]}]},
        {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a"}, {"key": "a"}]}]},
        {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "l", "type": "list", "fields": [
            {"key": "n", "type": "list", "fields": [{"key": "z"}]}]}]}]},
        {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "i", "type": "image", "dir": "/var/www"}]}]},
    ]
    for raw in bad:
        with pytest.raises(CmsError):
            parse_schema(json.dumps(raw))


def test_values_are_checked_and_undeclared_keys_kept():
    page = parse_schema(json.dumps(SCHEMA))["pages"][0]
    existing = {"title": "Old", "secret_flag": True, "hero": "/img/legacy.jpg"}
    merged, files, _ = apply_values(page, {"title": "New", "cta": "/contact", "theme": "dark",
                                           "hero": "/img/legacy.jpg",
                                           "services": [{"name": "Cut", "price": 20, "extra": 1}]},
                                    existing, no_upload)
    assert merged["secret_flag"] is True and merged["title"] == "New"
    assert merged["services"] == [{"extra": 1, "name": "Cut", "price": 20}] and not files
    for values in ({"title": ""}, {"title": "x" * 21}, {"title": "a\nb"}, {"cta": "javascript:alert(1)"},
                   {"theme": "neon"}, {"hero": "/etc/passwd"}, {"services": [{"name": "a"}] * 4},
                   {"services": [{"price": 1}]}, {"services": [{"name": "a", "price": "1"}]}):
        with pytest.raises(CmsError):
            apply_values(page, values, existing, no_upload)


def test_candidates_skip_tooling_files():
    files = [{"path": p, "size": 10} for p in ("package.json", "tsconfig.app.json", "node_modules/x/data.json",
                                               "src/content/home.json", "config.json", ".github/a.json",
                                               "public/site.webmanifest", "mas.cms.json")]
    assert [(c["path"], c["suggested"]) for c in content_candidates(files)] == [
        ("src/content/home.json", True), ("config.json", False)]


def test_infer_schema_from_content():
    content = {
        "heroTitle": "Bienvenue chez nous",
        "intro": "Un paragraphe\nsur deux lignes",
        "body": "Du **gras** et un [lien](https://example.com)",
        "hero": "/images/hero.jpg",
        "accent": "#ff8800",
        "contact_email": "hello@example.com",
        "website": "https://example.com",
        "opening": "2026-01-15",
        "show_banner": True,
        "seats": 12,
        "services": [{"name": "Coupe", "price": 20.5, "photo": "/images/coupe.webp"}, {"name": "Couleur sur cheveux longs"}],
        "tags": ["a", "b"],
        "seo": {"title": "x"},
    }
    schema, notes = infer_schema({"src/content/home.json": content}, lambda url: "public" + url)
    page = schema["pages"][0]
    assert page["id"] == "home" and page["file"] == "src/content/home.json"
    types = {f["key"]: f["type"] for f in page["fields"]}
    assert types == {"heroTitle": "text", "intro": "textarea", "body": "markdown", "hero": "image", "accent": "color",
                     "contact_email": "email", "website": "url", "opening": "date", "show_banner": "boolean",
                     "seats": "number", "services": "list"}
    hero = next(f for f in page["fields"] if f["key"] == "hero")
    assert hero["url"] == "/images" and hero["dir"] == "public/images"
    assert next(f for f in page["fields"] if f["key"] == "heroTitle")["label"] == "Hero title"
    services = next(f for f in page["fields"] if f["key"] == "services")
    assert [f["key"] for f in services["fields"]] == ["name", "price", "photo"]
    assert any("tags" in n for n in notes) and any("seo" in n for n in notes)
    # the generated schema accepts the existing content as it is
    merged, files, _ = apply_values(parse_schema(json.dumps(schema))["pages"][0], content, content, no_upload)
    assert merged == content and not files


def test_image_detection():
    assert detect_image(PNG) == "png"
    assert detect_image(b"\xff\xd8\xff\xe0rest") == "jpg"
    assert detect_image(b"<svg onload=alert(1)>") is None


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    db.create_app("site", name="Client site", repo="acme/site", branch="main", installation_id=7, type="static",
                  domain="client.example.com", auto_deploy=0)
    db.create_app("other", name="Other", repo="acme/other", branch="main", installation_id=7, type="static")
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    gh = FakeGitHub({"mas.cms.json": json.dumps(SCHEMA).encode(),
                     "content/home.json": b'{"title": "Hello", "keep": 1}\n'})
    app.state.cms.github = lambda: gh
    app.state.cms.deployer.enqueue = lambda app_id, trigger, actor, sha=None: 42
    return TestClient(app), db, gh


def login(client, username="admin", password=PASSWORD):
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


def make_editor(client, csrf, apps):
    r = client.post("/api/users", json={"username": "client"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    user = r.json()
    r = client.put(f"/api/users/{user['id']}/apps", json={"apps": apps}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200 and r.json()["apps"] == apps
    return user


def test_editor_is_confined_to_its_sites(env):
    client, db, gh = env
    user = make_editor(client, login(client), ["site"])
    client.post("/api/logout", headers={"x-csrf-token": login(client)})
    csrf = login(client, "client", user["password"])
    assert client.get("/api/session").json()["role"] == "editor"
    for path in ("/api/services", "/api/apps", "/api/history", "/api/users", "/api/supabase", "/api/apps/site/cms"):
        assert client.get(path).status_code == 403, path
    assert client.post("/api/scan", headers={"x-csrf-token": csrf}).status_code == 403
    assert [s["id"] for s in client.get("/api/cms/sites").json()] == ["site"]
    assert client.get("/api/cms/sites/other").status_code == 404
    page = client.get("/api/cms/sites/site").json()["pages"][0]
    assert page["values"]["title"] == "Hello"

    # upload + save: one commit with the JSON file and the image, then a deploy (auto_deploy is off)
    r = client.post("/api/cms/sites/site/uploads?name=My Photo.PNG", content=PNG,
                    headers={"x-csrf-token": csrf, "content-type": "image/png"})
    assert r.status_code == 200, r.text
    values = {**page["values"], "title": "Bonjour", "hero": r.json()["value"]}
    r = client.put("/api/cms/sites/site/pages/home", json={"values": values, "sha": page["sha"]},
                   headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["release_id"] == 42
    message, files = gh.commits[-1]
    assert "Edited by client" in message
    image_path = next(p for p in files if p.startswith("public/uploads/my-photo-"))
    saved = json.loads(gh.files["content/home.json"])
    assert saved["title"] == "Bonjour" and saved["keep"] == 1 and saved["hero"] == "/" + image_path[len("public/"):]
    assert client.get("/api/cms/sites/site/image", params={"src": saved["hero"]}).content == PNG

    # stale sha → conflict; not allowed on the other app; SVG refused
    r = client.put("/api/cms/sites/site/pages/home", json={"values": values, "sha": page["sha"]},
                   headers={"x-csrf-token": csrf})
    assert r.status_code == 409
    assert client.put("/api/cms/sites/other/pages/home", json={"values": {}},
                      headers={"x-csrf-token": csrf}).status_code == 404
    r = client.post("/api/cms/sites/site/uploads", content=b"<svg/>", headers={"x-csrf-token": csrf})
    assert r.status_code == 400
    assert any(h["action"] == "cms-save" and h["ok"] for h in db.history(None, 50))


def test_self_service_signup(env):
    client, db, _ = env
    body = {"username": "newclient", "password": "a long enough password"}
    assert client.get("/api/signup").json() == {"open": False, "code_required": False, "invite": None}
    assert client.post("/api/signup", json=body).status_code == 403  # closed by default

    csrf = login(client)
    r = client.put("/api/signup/settings", json={"open": True, "code": "welcome-2026"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    client.post("/api/logout", headers={"x-csrf-token": csrf})
    client.cookies.clear()
    assert client.get("/api/signup").json() == {"open": True, "code_required": True, "invite": None}
    assert client.post("/api/signup", json={**body, "code": "nope"}).status_code == 403
    assert client.post("/api/signup", json={**body, "code": "welcome-2026", "password": "short"}).status_code == 400
    assert client.post("/api/signup", json={**body, "username": "admin", "code": "welcome-2026"}).status_code == 409
    r = client.post("/api/signup", json={**body, "code": "welcome-2026"})
    assert r.status_code == 200 and r.json()["role"] == "editor"
    # signed in right away, as an editor with no site
    assert client.get("/api/session").json()["username"] == "newclient"
    assert client.get("/api/cms/sites").json() == []
    assert client.get("/api/services").status_code == 403
    assert client.get("/api/signup/settings").status_code == 403
    assert db.get_user("newclient")["role"] == "editor"
    # rate limit per address
    for i in range(5):
        client.post("/api/signup", json={**body, "username": f"spam{i}", "code": "welcome-2026"})
    assert client.post("/api/signup", json={**body, "username": "spam9", "code": "welcome-2026"}).status_code == 429


def test_one_time_invite_link(env):
    client, db, _ = env
    csrf = login(client)
    r = client.post("/api/invites", json={"apps": ["site"], "note": "Acme"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    path, invite_id = r.json()["path"], r.json()["id"]
    token = path.split("invite=")[1]
    assert [i["id"] for i in client.get("/api/invites").json()] == [invite_id]
    assert token not in json.dumps(client.get("/api/invites").json())  # only its hash is stored
    revoked = client.post("/api/invites", json={}, headers={"x-csrf-token": csrf}).json()
    assert client.delete(f"/api/invites/{revoked['id']}", headers={"x-csrf-token": csrf}).status_code == 200
    client.post("/api/logout", headers={"x-csrf-token": csrf})
    client.cookies.clear()

    # sign-up stays closed, the link works anyway, once, and gives the site right away
    assert client.get("/api/signup", params={"invite": token}).json()["invite"] is True
    body = {"username": "acme", "password": "a long enough password", "invite": token}
    assert client.post("/api/signup", json={**body, "invite": revoked["path"].split("invite=")[1]}).status_code == 403
    assert client.post("/api/signup", json={**body, "password": "short"}).status_code == 400  # not consumed
    r = client.post("/api/signup", json=body)
    assert r.status_code == 200, r.text
    assert [s["id"] for s in client.get("/api/cms/sites").json()] == ["site"]
    client.cookies.clear()
    assert client.get("/api/signup", params={"invite": token}).json()["invite"] is False
    assert client.post("/api/signup", json={**body, "username": "again"}).status_code == 403
    assert db.get_user("again") is None


def test_admin_sees_schema_problems_and_permissions(env):
    client, db, gh = env
    csrf = login(client)
    user = make_editor(client, csrf, [])
    gh.write = False
    info = client.get("/api/apps/site/cms").json()
    assert info["ok"] and info["can_write"] is False and info["pages"][0]["id"] == "home"
    r = client.put("/api/apps/site/cms/editors", json={"user_ids": [user["id"]]}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert client.get("/api/apps/site/cms").json()["editors"] == [{"id": user["id"], "username": "client",
                                                                  "allowed": True}]
    page = client.get("/api/cms/sites/site").json()["pages"][0]
    r = client.put("/api/cms/sites/site/pages/home", json={"values": {"title": "X"}, "sha": page["sha"]},
                   headers={"x-csrf-token": csrf})
    assert r.status_code == 400 and "Contents: Read and write" in r.json()["detail"]
    gh.files["mas.cms.json"] = b"{not json"
    gh.sha = "c9"
    assert "not valid JSON" in client.get("/api/apps/site/cms").json()["error"]
    # generate a schema from the content file and commit it
    gh.write = True
    found = client.get("/api/apps/site/cms/candidates").json()
    assert found["has_schema"] and [f["path"] for f in found["files"]] == ["content/home.json"]
    gen = client.post("/api/apps/site/cms/generate", json={"files": ["content/home.json"]},
                      headers={"x-csrf-token": csrf}).json()
    assert [f["key"] for f in gen["schema"]["pages"][0]["fields"]] == ["title", "keep"]
    r = client.put("/api/apps/site/cms/schema", json={"schema": gen["schema"]}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    assert json.loads(gh.files["mas.cms.json"]) == gen["schema"]
    assert client.get("/api/apps/site/cms").json()["ok"]
    bad = {"pages": [{"id": "x", "file": ".github/x.json", "fields": [{"key": "a"}]}]}
    assert client.put("/api/apps/site/cms/schema", json={"schema": bad}, headers={"x-csrf-token": csrf}).status_code == 400
    # editors only: an administrator cannot be deleted from the dashboard
    admin_id = db.get_user("admin")["id"]
    assert client.delete(f"/api/users/{admin_id}", headers={"x-csrf-token": csrf}).status_code == 404
    assert client.delete(f"/api/users/{user['id']}", headers={"x-csrf-token": csrf}).status_code == 200
    assert db.app_editors("site") == []
