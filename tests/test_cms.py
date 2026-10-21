"""CMS: schema validation, value checks, commits, editor accounts and access control."""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth, cms_supabase
from mas.api import create_app
from mas.cms import (CmsError, apply_values, content_candidates, detect_image, infer_schema, parse_schema, slugify,
                     sort_index)
from mas.config import Config
from mas.db import Database
from mas.envcrypt import encrypt_env, load_fernet
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
    "collections": [{
        "id": "articles", "label": "Articles", "item_label": "Article", "dir": "content/articles",
        "fields": [
            {"key": "title", "type": "text", "max": 100},
            {"key": "date", "type": "date"},
            {"key": "cover", "type": "image"},
            {"key": "excerpt", "type": "textarea", "max": 300},
            {"key": "body", "type": "markdown"},
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

    def list_dir(self, installation_id, repo, path, ref):
        return [{"name": p.rsplit("/", 1)[1], "path": p, "sha": self.read_file(0, repo, p, ref)[1]}
                for p in self.files if "/" in p and p.rsplit("/", 1)[0] == path]

    def commit_files(self, installation_id, repo, branch, files, message, parent):
        if not self.write:
            raise GitHubError("GitHub API POST /git/blobs: HTTP 403 Resource not accessible by integration")
        if parent != self.sha:
            raise GitHubError("GitHub API PATCH refs: HTTP 422 Update is not a fast forward")
        for path, data in files.items():
            if data is None:
                self.files.pop(path)
            else:
                self.files[path] = data
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


def test_collection_schema():
    c = parse_schema(json.dumps(SCHEMA))["collections"][0]
    assert c["title_field"] == "title" and c["sort_field"] == "date"
    assert c["index_fields"] == ["title", "date", "cover", "excerpt"]  # markdown bodies stay out of the index
    assert next(f for f in c["fields"] if f["key"] == "title")["required"]
    assert parse_schema(json.dumps({"collections": SCHEMA["collections"]}))["pages"] == []
    fields = [{"key": "title"}]
    bad = [
        {"collections": [{"id": "a", "dir": "../posts", "fields": fields}]},
        {"collections": [{"id": "a", "dir": "posts", "fields": [{"key": "slug"}]}]},
        {"collections": [{"id": "a", "dir": "posts", "fields": [{"key": "n", "type": "number"}]}]},  # no title
        {"collections": [{"id": "a", "dir": "posts", "fields": fields, "index_fields": ["nope"]}]},
        {"collections": [{"id": "a", "dir": "posts", "fields": fields},
                         {"id": "b", "dir": "posts/x", "fields": fields}]},
        {"pages": [{"id": "a", "file": "posts/a.json", "fields": fields}],
         "collections": [{"id": "b", "dir": "posts", "fields": fields}]},
        {"pages": [{"id": "a", "file": "a.json", "fields": fields}],
         "collections": [{"id": "a", "dir": "posts", "fields": fields}]},
    ]
    for raw in bad:
        with pytest.raises(CmsError):
            parse_schema(json.dumps(raw))


def test_slugs_and_order():
    assert slugify("Été à Paris : 10 idées !") == "ete-a-paris-10-idees"
    assert slugify("!!!") == "entry"
    c = parse_schema(json.dumps(SCHEMA))["collections"][0]
    entries = [{"slug": "b", "date": "2026-01-02"}, {"slug": "z"}, {"slug": "a", "date": "2026-01-02"},
               {"slug": "c", "date": "2026-03-01"}]
    assert [e["slug"] for e in sort_index(c, entries)] == ["c", "a", "b", "z"]


def test_articles_add_edit_delete(env):
    client, db, gh = env
    user = make_editor(client, login(client), ["site"])
    client.post("/api/logout", headers={"x-csrf-token": login(client)})
    csrf = login(client, "client", user["password"])
    h = {"x-csrf-token": csrf}
    gh.files["content/articles/hand-written.json"] = b'{"title": "By hand", "date": "2025-12-01"}'

    site = client.get("/api/cms/sites/site").json()
    articles = site["collections"][0]
    assert articles["id"] == "articles" and [e["slug"] for e in articles["entries"]] == ["hand-written"]
    assert articles["entries"][0]["title"] == "By hand"  # indexed even without index.json

    base = "/api/cms/sites/site/collections/articles/entries"
    upload = client.post("/api/cms/sites/site/uploads?name=cover.png", content=PNG, headers=h).json()["value"]
    first = {"title": "Été à Paris", "date": "2026-07-01", "cover": upload, "excerpt": "Short", "body": "# Hi"}
    r = client.post(base, json={"values": first, "note": ""}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "ete-a-paris" and r.json()["release_id"] == 42
    message, files = gh.commits[-1]
    assert message.startswith("cms(articles): add Été à Paris") and "Edited by client" in message
    assert any(p.startswith("public/uploads/cover-") for p in files)
    saved = json.loads(gh.files["content/articles/ete-a-paris.json"])
    assert saved["slug"] == "ete-a-paris" and saved["body"] == "# Hi" and saved["cover"].startswith("/uploads/cover-")

    # same title → another address; chosen address; bad and taken addresses refused
    again = client.post(base, json={"values": {**first, "cover": "", "date": "2026-01-01"}}, headers=h)
    assert again.json()["slug"] == "ete-a-paris-2"
    chosen = client.post(base, json={"values": {"title": "Later"}, "slug": "later-news"}, headers=h)
    assert chosen.json()["slug"] == "later-news"
    assert client.post(base, json={"values": {"title": "X"}, "slug": "Bad Slug"}, headers=h).status_code == 400
    assert client.post(base, json={"values": {"title": "X"}, "slug": "later-news"}, headers=h).status_code == 400
    assert client.post(base, json={"values": {"date": "2026-01-01"}}, headers=h).status_code == 400  # title required

    index = json.loads(gh.files["content/articles/index.json"])
    assert [e["slug"] for e in index] == ["ete-a-paris", "ete-a-paris-2", "hand-written", "later-news"]
    assert "body" not in index[0] and index[0]["excerpt"] == "Short"

    # edit: sha checked, the address stays, the index follows
    entry = client.get(f"{base}/ete-a-paris").json()
    assert entry["values"]["title"] == "Été à Paris"
    edited = {**entry["values"], "title": "Summer", "date": "2025-01-01"}
    r = client.put(f"{base}/ete-a-paris", json={"values": edited, "sha": entry["sha"]}, headers=h)
    assert r.status_code == 200 and r.json()["slug"] == "ete-a-paris"
    stale = client.put(f"{base}/ete-a-paris", json={"values": entry["values"], "sha": entry["sha"]}, headers=h)
    assert stale.status_code == 409
    index = json.loads(gh.files["content/articles/index.json"])
    assert [e["slug"] for e in index] == ["ete-a-paris-2", "hand-written", "ete-a-paris", "later-news"]
    assert index[2]["title"] == "Summer"

    # delete
    entry = client.get(f"{base}/later-news").json()
    assert client.delete(f"{base}/later-news", params={"sha": "stale"}, headers=h).status_code == 409
    r = client.delete(f"{base}/later-news", params={"sha": entry["sha"]}, headers=h)
    assert r.status_code == 200, r.text
    assert "content/articles/later-news.json" not in gh.files
    assert "later-news" not in gh.files["content/articles/index.json"].decode()
    assert client.get(f"{base}/later-news").status_code == 400
    assert client.get(f"{base}/..%2Fhome").status_code in (400, 404)
    assert any(e["action"] == "cms-delete" and e["ok"] for e in db.history(None, 50))

    # confined to its sites and to the declared collections
    other = client.post("/api/cms/sites/other/collections/articles/entries", json={"values": first}, headers=h)
    assert other.status_code == 404
    unknown = client.post("/api/cms/sites/site/collections/secrets/entries", json={"values": first}, headers=h)
    assert unknown.status_code == 400


def test_regenerating_pages_keeps_collections(env):
    client, _, gh = env
    csrf = login(client)
    gh.files["content/articles/a.json"] = b'{"title": "A"}'
    found = client.get("/api/apps/site/cms/candidates").json()
    assert [f["path"] for f in found["files"]] == ["content/home.json"]  # entries are not pages
    gen = client.post("/api/apps/site/cms/generate", json={"files": ["content/home.json"]},
                      headers={"x-csrf-token": csrf}).json()
    assert gen["schema"]["collections"] == SCHEMA["collections"]
    info = client.get("/api/apps/site/cms").json()
    assert info["collections"] == [{"id": "articles", "label": "Articles", "storage": "git", "dir": "content/articles",
                                    "table": None, "fields": 5, "error": None}]


class FakeSupabase:
    """PostgREST and Storage in memory, enough for the CMS: one table, one bucket."""

    def __init__(self, table: str = "news") -> None:
        self.table = table
        self.exists = True
        self.rows: dict[str, dict] = {}
        self.objects: dict[str, bytes] = {}
        self.bucket = False
        self.urls: list[str] = []
        self.tick = 0

    def __call__(self, method, url, headers, body):
        parts = urlsplit(url)
        path, query = parts.path, dict(parse_qsl(parts.query))
        self.urls.append(url)
        if headers.get("apikey") != "service-key":
            return 401, b'{"message": "Invalid API key"}'
        if path.startswith("/storage/v1/bucket"):
            self.bucket = True
            return 200, b"{}"
        if path.startswith("/storage/v1/object/"):
            key = unquote(path[len("/storage/v1/object/"):])
            if method == "POST":
                if not self.bucket:
                    return 404, b'{"message": "Bucket not found"}'
                self.objects[key] = body
                return 200, b"{}"
            return (200, self.objects[key]) if key in self.objects else (404, b"{}")
        assert path == f"/rest/v1/{self.table}", path
        if not self.exists:
            missing = {"code": "PGRST205", "message": "Could not find the table in the schema cache"}
            return 404, json.dumps(missing).encode()

        def match(row):
            return all(str(row.get(k)) == v[3:] for k, v in query.items() if v.startswith("eq."))
        if method == "GET":
            rows = sorted((r for r in self.rows.values() if match(r)), key=lambda r: r["slug"])
            if query.get("order", "").startswith("date.desc"):
                rows.sort(key=lambda r: r.get("date") or "", reverse=True)
            cols = query["select"].split(",")
            return 200, json.dumps([r if cols == ["*"] else {c: r.get(c) for c in cols} for r in rows]).encode()
        data = json.loads(body) if body else None
        self.tick += 1
        if method == "POST":
            if data["slug"] in self.rows:
                return 409, b'{"code": "23505", "message": "duplicate key"}'
            row = {**data, "created_at": f"t{self.tick}", "updated_at": f"t{self.tick}"}
            self.rows[data["slug"]] = row
            return 201, json.dumps([row]).encode()
        found = [r for r in self.rows.values() if match(r)]
        if method == "PATCH":
            for r in found:
                r.update(data, updated_at=f"t{self.tick}")
            return 200, json.dumps(found).encode()
        for r in found:
            del self.rows[r["slug"]]
        return 200, json.dumps(found).encode()


SUPABASE_SCHEMA = {"collections": [{
    "id": "news", "label": "News", "storage": "supabase",
    "fields": [{"key": "title", "type": "text"}, {"key": "date", "type": "date"}, {"key": "cover", "type": "image"},
               {"key": "body", "type": "markdown"}, {"key": "views", "type": "number", "integer": True}],
}]}


def test_supabase_collection_schema_and_sql():
    c = parse_schema(json.dumps(SUPABASE_SCHEMA))["collections"][0]
    assert c["storage"] == "supabase" and c["table"] == "news" and c["dir"] is None
    sql = cms_supabase.table_sql(c)
    assert "slug text primary key" in sql and "views bigint" in sql and "date date" in sql
    assert "enable row level security" in sql and "for select to anon" in sql
    for fields in ([{"key": "Title"}], [{"key": "title"}, {"key": "updated_at"}], [{"key": "my-title"}]):
        with pytest.raises(CmsError):
            parse_schema(json.dumps({"collections": [{"id": "n", "storage": "supabase", "fields": fields}]}))
    with pytest.raises(CmsError):
        parse_schema(json.dumps({"collections": [{"id": "n", "storage": "mysql", "dir": "x",
                                                  "fields": [{"key": "t"}]}]}))


def test_articles_in_supabase(env):
    client, db, gh = env
    gh.files["mas.cms.json"] = json.dumps(SUPABASE_SCHEMA).encode()
    gh.sha = "c-supabase"
    cms = client.app.state.cms
    fake = FakeSupabase()
    cms.supabase_opener = fake
    fernet = load_fernet(cms.deployer.host, cms.deployer.config.secret_key_path)
    db.update_app("site", env=encrypt_env(fernet, {"SUPABASE_URL": "https://db.example.com",
                                                   "SUPABASE_SERVICE_ROLE_KEY": "service-key"}))
    csrf = login(client)
    h = {"x-csrf-token": csrf}

    # the table is missing: the editor says so, the admin gets the SQL (external project: no button)
    fake.exists = False
    assert "does not exist yet" in client.get("/api/cms/sites/site").json()["collections"][0]["error"]
    info = client.get("/api/apps/site/cms").json()["collections"][0]
    assert info["storage"] == "supabase" and "create table if not exists public.news" in info["sql"]
    assert info["local"] is False and info["url"] == "https://db.example.com"
    assert client.post("/api/apps/site/cms/collections/news/table", headers=h).status_code == 400
    fake.exists = True
    assert client.get("/api/apps/site/cms").json()["collections"][0]["error"] is None

    base = "/api/cms/sites/site/collections/news/entries"
    upload = client.post("/api/cms/sites/site/uploads?name=cover.png", content=PNG, headers=h).json()["value"]
    r = client.post(base, json={"values": {"title": "Été", "date": "2026-07-01", "cover": upload, "body": "# Hi"}},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"slug": "ete", "commit": None, "unchanged": False, "release_id": None, "instant": True}
    assert not gh.commits  # nothing in the repository, nothing to rebuild
    assert all(u.startswith("https://db.example.com/") for u in fake.urls)
    row = fake.rows["ete"]
    assert row["body"] == "# Hi" and row["views"] is None
    assert row["cover"].startswith("https://db.example.com/storage/v1/object/public/mas-cms/news/cover-")
    assert fake.bucket and list(fake.objects) == ["mas-cms/" + row["cover"].rsplit("/mas-cms/", 1)[1]]
    assert client.get("/api/cms/sites/site/image", params={"src": row["cover"]}).content == PNG

    client.post(base, json={"values": {"title": "Été", "date": "2026-08-01"}}, headers=h)
    listed = client.get("/api/cms/sites/site").json()["collections"][0]
    assert [e["slug"] for e in listed["entries"]] == ["ete-2", "ete"] and "body" not in listed["entries"][0]

    entry = client.get(f"{base}/ete").json()
    assert entry["values"]["views"] is None and entry["sha"] == row["updated_at"]
    r = client.put(f"{base}/ete", json={"values": {**entry["values"], "views": 3}, "sha": entry["sha"]}, headers=h)
    assert r.status_code == 200 and fake.rows["ete"]["views"] == 3 and fake.rows["ete"]["cover"] == row["cover"]
    stale = client.put(f"{base}/ete", json={"values": entry["values"], "sha": entry["sha"]}, headers=h)
    assert stale.status_code == 409
    fresh = client.get(f"{base}/ete").json()
    same = client.put(f"{base}/ete", json={"values": fresh["values"], "sha": fresh["sha"]}, headers=h).json()
    assert same["unchanged"] is True

    assert client.delete(f"{base}/ete", params={"sha": entry["sha"]}, headers=h).status_code == 409
    assert client.delete(f"{base}/ete", params={"sha": fresh["sha"]}, headers=h).status_code == 200
    assert "ete" not in fake.rows
    assert any(e["action"] == "cms-delete" and e["detail"].endswith("supabase") for e in db.history(None, 50))

    # a wrong key is reported as such
    db.update_app("site", env=encrypt_env(fernet, {"SUPABASE_URL": "https://db.example.com",
                                                   "SUPABASE_SERVICE_ROLE_KEY": "wrong"}))
    assert "service_role key" in client.get("/api/cms/sites/site").json()["collections"][0]["error"]
    # no Supabase at all
    db.update_app("site", env=None)
    assert "deploy Supabase" in client.get("/api/cms/sites/site").json()["collections"][0]["error"]


def test_supabase_of_this_server(env):
    client, db, gh = env
    gh.files["mas.cms.json"] = json.dumps(SUPABASE_SCHEMA).encode()
    gh.sha = "c-local"
    cms = client.app.state.cms
    fake = FakeSupabase()
    cms.supabase_opener = fake
    fernet = load_fernet(cms.deployer.host, cms.deployer.config.secret_key_path)
    db.save_supabase("supabase", status="running", dir="/srv/mas/supabase/supabase", domain="db.example.com",
                     ports=json.dumps({"kong_http": 7100, "postgres": 7102}),
                     secrets=encrypt_env(fernet, {"SERVICE_ROLE_KEY": "service-key"}))
    h = {"x-csrf-token": login(client)}
    # no variable on the app: the instance of this server, reached on its loopback port, links on its domain
    info = client.get("/api/apps/site/cms").json()["collections"][0]
    assert info["local"] is True and info["url"] == "https://db.example.com" and info["error"] is None
    assert fake.urls[-1].startswith("http://127.0.0.1:7100/rest/v1/news?")
    r = client.post("/api/cms/sites/site/collections/news/entries", json={"values": {"title": "Hello"}}, headers=h)
    assert r.status_code == 200 and fake.rows["hello"]["title"] == "Hello"
    # the table is created with psql in the database container
    host = cms.deployer.host
    host._commands["docker exec supabase-db psql *"] = {"returncode": 0, "output": "CREATE TABLE"}
    r = client.post("/api/apps/site/cms/collections/news/table", headers=h)
    assert r.status_code == 200, r.text
    psql = next(argv for argv in host.executed if argv[:4] == ["docker", "exec", "supabase-db", "psql"])
    assert "create table if not exists public.news" in psql[-1]
    host._commands["docker exec supabase-db psql *"] = {"returncode": 1, "stderr": "ERROR: boom"}
    r = client.post("/api/apps/site/cms/collections/news/table", headers=h)
    assert r.status_code == 400 and "boom" in r.json()["detail"]
