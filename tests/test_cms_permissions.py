"""Permissions per site: an invitation (or an administrator) chooses what an account may do on each site."""

from __future__ import annotations

from test_api_tokens import make as make_token
from test_cms import env, login  # noqa: F401  (env is a fixture)

from mas.db import SITE_PERMS

PW = "a long enough passphrase 77"
BASE = "/api/cms/sites/site"


def invite(client, admin, access, role="editor"):
    r = client.post("/api/invites", json={"access": access, "role": role, "note": "client"}, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["path"].split("invite=")[1]


def signup(client, token, username="client"):
    client.cookies.clear()
    r = client.post("/api/signup", json={"username": username, "password": PW, "invite": token})
    assert r.status_code == 200, r.text
    return {"x-csrf-token": r.json()["csrf"]}


def test_a_client_invited_for_statistics_only_sees_them_and_changes_nothing(env):
    client, db, gh = env
    admin = {"x-csrf-token": login(client)}
    token = invite(client, admin, {"site": ["stats"]})
    listed = client.get("/api/invites", headers=admin).json()[0]
    assert listed["access"] == {"site": ["stats"]} and listed["apps"] == ["site"]
    h = signup(client, token)

    sites = client.get("/api/cms/sites", headers=h).json()
    assert [(s["id"], s["perms"]) for s in sites] == [("site", ["stats"])]
    assert client.get(f"{BASE}/stats", headers=h).status_code == 200
    assert client.get(f"{BASE}/uptime", headers=h).status_code == 200
    assert client.get(f"{BASE}/overview", headers=h).json() == {"pages": [], "collections": []}
    for path in ("", "/media", "/changes", "/compliance", "/legal", "/cookies", "/seo", "/search?q=a",
                 "/collections/articles/entries/x"):
        r = client.get(f"{BASE}{path}", headers=h)
        assert r.status_code == 403, (path, r.status_code)
    for method, path, body in (
        ("put", "/pages/home", {"values": {"title": "X"}}),
        ("post", "/collections/articles/entries", {"values": {"title": "X"}, "status": "draft"}),
        ("put", "/cookies", {}),
        ("put", "/goals", {"paths": []}),
        ("post", "/compliance/run", None),
        ("post", "/media/delete", {"value": "/x.png"}),
        ("post", "/seo/audit", None),
    ):
        r = getattr(client, method)(f"{BASE}{path}", headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.status_code, r.text)
    assert gh.commits == []
    # the dashboard and the history do not tell what changed in the content
    dash = client.get("/api/cms/dashboard", headers=h).json()
    assert dash["activity"] == [] and dash["sites"][0]["scheduled"] == [] and dash["sites"][0]["compliance"] is None
    assert client.get("/api/cms/activity", headers=h).json() == []
    # another site stays invisible
    assert client.get("/api/cms/sites/other/stats", headers=h).status_code == 404


def test_permissions_are_chosen_site_by_site_and_publish_brings_content(env):
    client, db, gh = env
    admin = {"x-csrf-token": login(client)}
    token = invite(client, admin, {"site": ["publish"], "other": ["stats", "seo"]})
    signup(client, token)
    uid = db.get_user("client")["id"]
    assert db.user_access(uid) == {"site": ["content", "publish"], "other": ["stats", "seo"]}

    client.cookies.clear()
    admin = {"x-csrf-token": login(client)}
    url = f"/api/users/{uid}/access"
    assert client.put(url, json={"access": {"site": ["root"]}}, headers=admin).status_code == 400
    assert client.put(url, json={"access": {"nope": ["stats"]}}, headers=admin).status_code == 400
    r = client.put(url, json={"access": {"site": ["content", "stats"], "other": []}}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["access"] == {"site": ["content", "stats"]} and r.json()["apps"] == ["site"]
    # without "publish" the account writes drafts only
    page = client.get(BASE, headers=admin).json()["pages"][0]
    h = {"x-csrf-token": login(client, "client", PW)}
    r = client.put(f"{BASE}/pages/home", json={"values": {"title": "X"}, "sha": page["sha"]}, headers=h)
    assert r.status_code == 403 and "drafts only" in r.json()["detail"]
    assert client.get(BASE, headers=h).status_code == 200


def test_an_author_cannot_publish_unless_given_it_and_old_accounts_keep_their_rights(env):
    client, db, gh = env
    admin = {"x-csrf-token": login(client)}
    made = client.post("/api/users", json={"username": "anna", "role": "author"}, headers=admin).json()
    client.put(f"/api/users/{made['id']}/apps", json={"apps": ["site"]}, headers=admin)
    # an access without stored permissions is the role's: an author never publishes
    assert db.user_access(made["id"]) == {"site": ["content", "stats", "seo", "privacy"]}
    r = client.put(f"/api/users/{made['id']}/access", json={"access": {"site": ["publish"]}}, headers=admin)
    assert r.json()["role"] == "editor" and r.json()["access"] == {"site": ["content", "publish"]}
    # changing the list of sites keeps the permissions of a site already given
    client.put(f"/api/users/{made['id']}/apps", json={"apps": ["site", "other"]}, headers=admin)
    assert db.user_access(made["id"]) == {"site": ["content", "publish"], "other": list(SITE_PERMS)}
    client.put("/api/apps/other/cms/editors", json={"user_ids": [made["id"]]}, headers=admin)
    assert db.user_access(made["id"])["site"] == ["content", "publish"]


def test_access_changes_are_for_administrators_only(env):
    client, db, gh = env
    admin = {"x-csrf-token": login(client)}
    token = invite(client, admin, {"site": ["stats"]})
    h = signup(client, token)
    uid = db.get_user("client")["id"]
    assert client.put(f"/api/users/{uid}/access", json={"access": {"site": list(SITE_PERMS)}},
                      headers=h).status_code in (401, 403)
    assert client.post("/api/invites", json={"access": {"site": ["publish"]}}, headers=h).status_code in (401, 403)
    # nor can an API token, whatever its scope
    bearer = make_token(db, db.get_user("admin")["id"], "write")
    client.cookies.clear()
    assert client.put(f"/api/users/{uid}/access", json={"access": {"site": ["publish"]}},
                      headers=bearer).status_code == 403
    assert db.user_access(uid) == {"site": ["stats"]}


def test_letting_an_author_publish_adds_publish_where_they_write(env):
    client, db, gh = env
    admin = {"x-csrf-token": login(client)}
    made = client.post("/api/users", json={"username": "anna", "role": "author"}, headers=admin).json()
    client.put(f"/api/users/{made['id']}/access", json={"access": {"site": ["content"], "other": ["stats"]}},
               headers=admin)
    r = client.put(f"/api/users/{made['id']}/role", json={"role": "editor"}, headers=admin)
    assert r.json()["access"] == {"site": ["content", "publish"], "other": ["stats"]}
