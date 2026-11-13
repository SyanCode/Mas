"""Authors write drafts; editors publish; neither reaches the settings of the site."""

from __future__ import annotations

import copy
import json

from test_cms import SCHEMA, env, login  # noqa: F401  (env is a fixture)

from mas import auth

BASE = "/api/cms/sites/site/collections/articles/entries"
PW = "correct horse battery"


def setup(env_):
    client, db, gh = env_
    schema = copy.deepcopy(SCHEMA)
    schema["collections"][0]["drafts"] = True
    gh.files["mas.cms.json"] = json.dumps(schema).encode()
    client.app.state.cms._schemas.clear()
    admin = {"x-csrf-token": login(client)}
    out = {}
    for name, role in (("anna", "author"), ("ed", "editor")):
        uid = db.create_user(name, auth.hash_password(PW), role=role)
        db.set_user_apps(uid, ["site"])
    client.cookies.clear()
    return client, db, gh, admin


def as_(client, name):
    client.cookies.clear()
    return {"x-csrf-token": login(client, name, PW)}


def test_author_writes_drafts_only(env):
    client, db, gh, _ = setup(env)
    a = as_(client, "anna")
    r = client.post(BASE, json={"values": {"title": "Idea"}, "status": "draft"}, headers=a)
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    # not published, not scheduled, not saved without a status
    assert client.post(BASE, json={"values": {"title": "Live"}, "status": "published"}, headers=a).status_code == 403
    assert client.post(BASE, json={"values": {"title": "Live"}}, headers=a).status_code == 403
    assert client.post(f"{BASE}/{slug}/status", json={"status": "published"}, headers=a).status_code == 403
    # she can keep working on her draft
    entry = client.get(f"{BASE}/{slug}", headers=a).json()
    r = client.put(f"{BASE}/{slug}", json={"values": {**entry["values"], "title": "Idea 2"}, "sha": entry["sha"],
                                           "status": "draft"}, headers=a)
    assert r.status_code == 200, r.text
    # an editor publishes it
    e = as_(client, "ed")
    assert client.post(f"{BASE}/{slug}/status", json={"status": "published"}, headers=e).status_code == 200
    # a published entry is no longer hers to edit, nor to delete
    entry = client.get(f"{BASE}/{slug}", headers=e).json()
    a = as_(client, "anna")
    assert client.put(f"{BASE}/{slug}", json={"values": entry["values"], "sha": entry["sha"], "status": "draft"},
                      headers=a).status_code == 403
    assert client.delete(f"{BASE}/{slug}?sha={entry['sha']}", headers=a).status_code == 403


def test_author_cannot_change_the_site(env):
    client, db, gh, _ = setup(env)
    a = as_(client, "anna")
    base = "/api/cms/sites/site"
    for method, path, body in (
        ("put", f"{base}/pages/home", {"values": {}}),
        ("put", f"{base}/cookies", {}),
        ("put", f"{base}/goals", {"paths": []}),
        ("put", f"{base}/legal", {}),
        ("post", f"{base}/legal/publish", {}),
        ("post", f"{base}/compliance/run", None),
        ("post", f"{base}/changes/1/revert", None),
        ("post", f"{base}/media/delete", {"value": "/x.png"}),
    ):
        r = getattr(client, method)(path, headers=a, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.status_code, r.text)
    # reading is allowed
    assert client.get(base, headers=a).status_code == 200
    assert client.get(f"{base}/compliance", headers=a).status_code == 200
    # an editor is not blocked by this rule
    e = as_(client, "ed")
    assert client.get(f"{base}/compliance", headers=e).status_code == 200
    assert client.put(f"{base}/cookies", json={}, headers=e).status_code != 403


def test_roles_are_managed_by_administrators(env):
    client, db, gh, admin = setup(env)
    client.cookies.clear()
    admin = {"x-csrf-token": login(client)}
    made = client.post("/api/users", json={"username": "bob", "role": "author"}, headers=admin)
    assert made.status_code == 200 and made.json()["role"] == "author"
    assert client.post("/api/users", json={"username": "eve", "role": "admin"}, headers=admin).status_code == 400
    uid = made.json()["id"]
    assert client.put(f"/api/users/{uid}/role", json={"role": "editor"}, headers=admin).json()["role"] == "editor"
    assert client.put(f"/api/users/{uid}/role", json={"role": "admin"}, headers=admin).status_code == 400
    assert client.put("/api/users/1/role", json={"role": "author"}, headers=admin).status_code == 404  # an administrator

    # an invitation carries the role
    inv = client.post("/api/invites", json={"apps": ["site"], "role": "author", "note": "intern"}, headers=admin)
    assert inv.status_code == 200
    assert [i["role"] for i in client.get("/api/invites", headers=admin).json()] == ["author"]
    token = inv.json()["path"].split("invite=")[1]
    client.cookies.clear()
    r = client.post("/api/signup", json={"username": "newbie", "password": "a long enough passphrase 77", "invite": token})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "author"
    # and the author cannot reach administration
    assert client.get("/api/users", headers={"x-csrf-token": r.json()["csrf"]}).status_code in (401, 403)
