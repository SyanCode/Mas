"""Private preview links: a draft readable by someone without an account, for a while."""

from __future__ import annotations

import copy
import json
import time

from test_cms import PNG, SCHEMA, env, login  # noqa: F401  (env is a fixture)

from mas import preview

BASE = "/api/cms/sites/site/collections/articles/entries"


def prepare(env_):
    client, db, gh = env_
    schema = copy.deepcopy(SCHEMA)
    schema["collections"][0]["drafts"] = True
    gh.files["mas.cms.json"] = json.dumps(schema).encode()
    client.app.state.cms._schemas.clear()
    h = {"x-csrf-token": login(client)}
    r = client.post(BASE, json={"values": {"title": "Secret <b>plan</b>"}, "status": "draft"}, headers=h)
    assert r.status_code == 200, r.text
    return client, db, gh, h, r.json()["slug"]


def test_a_draft_is_readable_through_its_link_only(env):
    client, db, gh, h, slug = prepare(env)
    r = client.post(f"{BASE}/{slug}/preview", json={"days": 3}, headers=h)
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    client.cookies.clear()  # nobody is signed in
    page = client.get(path)
    assert page.status_code == 200
    assert "Secret &lt;b&gt;plan&lt;/b&gt;" in page.text and "<b>plan</b>" not in page.text  # escaped
    assert "not published yet" in page.text and "noindex" in page.text
    assert page.headers["x-robots-tag"].startswith("noindex") and "no-store" in page.headers["cache-control"]
    assert "default-src 'none'" in page.headers["content-security-policy"]

    # a forged or altered token, and another site's secret, get nothing
    assert client.get(path[:-3] + "xyz").status_code == 404
    body, sig = path.rsplit("/", 1)[1].split(".")
    assert client.get(f"/preview/{body}.{'A' * len(sig)}").status_code == 404
    assert client.get("/preview/nonsense").status_code == 404


def test_expiry_and_revocation(env):
    client, db, gh, h, slug = prepare(env)
    token, _ = preview.make(db, "site", "articles", slug, 1, now=time.time() - 2 * 86400)  # expired yesterday
    client.cookies.clear()
    assert client.get(f"/preview/{token}").status_code == 404
    live = client.post(f"{BASE}/{slug}/preview", json={"days": 7}, headers={"x-csrf-token": login(client)}).json()["path"]
    client.cookies.clear()
    assert client.get(live).status_code == 200
    h = {"x-csrf-token": login(client)}
    assert client.post("/api/cms/sites/site/preview/revoke", headers=h).status_code == 200
    client.cookies.clear()
    assert client.get(live).status_code == 404


def test_the_link_is_for_that_entry_and_an_author_may_share_it(env):
    client, db, gh, h, slug = prepare(env)
    assert client.post(f"{BASE}/nope/preview", json={}, headers=h).status_code == 400
    # a link of another site cannot be made from this one
    assert client.post("/api/cms/sites/other/collections/articles/entries/x/preview", json={}, headers=h).status_code in (400, 404)


def test_markdown_is_escaped_and_limited():
    out = preview.markdown("# Title\n\nHello **bold** and *it* [x](https://a.b/c) [bad](javascript:alert(1))\n\n- one\n- two\n\n<script>alert(1)</script>")
    assert "<h2>Title</h2>" in out and "<strong>bold</strong>" in out and "<em>it</em>" in out
    assert '<a href="https://a.b/c"' in out and "javascript:" not in out.replace("[bad](javascript:alert(1))", "")
    assert "<li>one</li>" in out and "<script>" not in out and "&lt;script&gt;" in out


def test_search_reads_the_words_of_the_content(env):
    client, db, gh = env
    h = {"x-csrf-token": login(client)}
    client.post(BASE, json={"values": {"title": "Opening hours"}}, headers=h)
    client.post(BASE, json={"values": {"title": "Our bakery", "body": "We bake sourdough bread every morning."}}, headers=h)
    cms = client.app.state.cms
    cms._searches.clear()
    hits = client.get("/api/cms/sites/site/search?q=sourdough", headers=h).json()
    assert [x["title"] for x in hits] == ["Our bakery"] and "sourdough" in hits[0]["extract"]
    assert client.get("/api/cms/sites/site/search?q=opening%20hours", headers=h).json()[0]["title"] == "Opening hours"
    assert client.get("/api/cms/sites/site/search?q=zzzz", headers=h).json() == []
    assert client.get("/api/cms/sites/site/search?q=a", headers=h).json() == []  # too short to mean anything
    from mas import auth
    db.create_user("ed", auth.hash_password("correct horse battery"), role="editor")  # no site granted
    client.cookies.clear()
    ed = {"x-csrf-token": login(client, "ed", "correct horse battery")}
    assert client.get("/api/cms/sites/site/search?q=bread", headers=ed).status_code == 404
