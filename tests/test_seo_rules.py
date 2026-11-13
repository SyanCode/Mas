"""Redirects, robots.txt and sitemap written into the nginx site, and the look at the live site."""

from __future__ import annotations

import json

import pytest
import server
from fastapi.testclient import TestClient
from test_cms import PNG, SCHEMA, FakeGitHub, PASSWORD, login  # noqa: F401

from mas import auth, seo, siterules, templates
from mas.api import create_app
from mas.compliance import Response
from mas.config import Config
from mas.db import Database
from mas.deploy import vhost_paths
from mas.host import FixtureHost
from mas.siterules import RulesError, clean, collect_urls, nginx_rules, sitemap_xml

BASE = "/api/cms/sites/site"


def page(title="", desc="", h1="Hello", extra="", lang="fr"):
    head = (f"<title>{title}</title>" if title else "") + (f'<meta name="description" content="{desc}">' if desc else "")
    return f'<html lang="{lang}"><head>{head}<meta name="viewport" content="width=device-width">{extra}</head><body><h1>{h1}</h1></body></html>'


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands = {"nginx -t": {"output": ""}, "systemctl reload nginx": {"output": ""}, **host._commands}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    db.create_app("site", name="Client site", repo="acme/site", branch="main", installation_id=7, type="static",
                  domain="client.example.com", auto_deploy=0)
    db.update_app("site", mode="managed")
    host.write_text(vhost_paths("site")[0], templates.vhost("site", "client.example.com", templates.proxy_body(3100, "/srv/mas/pages"), True))
    pages: dict[str, Response] = {}

    def fetch(url, follow=True):
        known = {k.rstrip("/"): v for k, v in pages.items()}
        return known.get(url.rstrip("/"), Response(url, 404, [("content-type", "text/html")], b"not found"))

    app = create_app(host, Config(cookie_secure=False), db, background=False, seo_fetch=fetch)
    gh = FakeGitHub({"mas.cms.json": json.dumps(SCHEMA).encode(), "content/home.json": b'{"title": "Hello"}\n',
                     "content/articles/hello.json": b'{"title": "Hello", "date": "2026-05-01"}\n',
                     "content/articles/index.json": b'[{"slug": "hello", "title": "Hello", "date": "2026-05-01"}]\n'})
    app.state.cms.github = lambda: gh
    return TestClient(app), db, host, pages


def html(url, body, status=200, ctype="text/html"):
    return Response(url, status, [("content-type", ctype)], body.encode())


def test_validation_keeps_nginx_syntax_out_of_reach():
    ok = clean({"redirects": [{"from": "/old", "to": "/new", "code": 301}, {"from": "/blog/*", "to": "/news/*", "code": 302},
                              {"from": "/x", "to": "https://example.org/y?a=1", "code": 301}]})
    assert len(ok["redirects"]) == 3
    for bad in ("/a b", "/a;b", "/a{b}", "/a$b", "/a\"b", "/a'b", "//evil", "old", "/*", "/a*b", "/a\nb"):
        with pytest.raises(RulesError):
            clean({"redirects": [{"from": bad, "to": "/new", "code": 301}]})
    for bad in ("/n;ew", "javascript:alert(1)", "https://x.com/a b", "https://x.com/{a}", "https://exa mple.com", "/a$1"):
        with pytest.raises(RulesError):
            clean({"redirects": [{"from": "/old", "to": bad, "code": 301}]})
    with pytest.raises(RulesError):
        clean({"redirects": [{"from": "/old", "to": "/old", "code": 301}]})
    with pytest.raises(RulesError):
        clean({"redirects": [{"from": "/old", "to": "/a", "code": 301}, {"from": "/old", "to": "/b", "code": 301}]})
    with pytest.raises(RulesError):
        clean({"redirects": [{"from": "/old", "to": "/new", "code": 307}]})
    with pytest.raises(RulesError):
        clean({"sitemap": {"enabled": True, "pages": [], "collections": {"articles": "/blog/"}}})
    with pytest.raises(RulesError):
        clean({"robots": {"enabled": True, "text": "User-agent: *\x00"}})


def test_nginx_lines_and_sitemap_xml():
    cfg = clean({"redirects": [{"from": "/old", "to": "/new", "code": 301}, {"from": "/blog/*", "to": "/news/*", "code": 302},
                               {"from": "/a.html", "to": "https://example.org/", "code": 301}],
                 "robots": {"enabled": True, "text": ""}, "sitemap": {"enabled": True, "pages": ["/"], "collections": {}}})
    text = nginx_rules("site", cfg)
    assert "location = /old { return 301 /new; }" in text
    assert "location ^~ /blog/ { rewrite ^/blog/(.*)$ /news/$1 redirect; }" in text
    assert "location = /a.html { return 301 https://example.org/; }" in text
    assert "alias /etc/nginx/mas-site/site/robots.txt;" in text and "alias /etc/nginx/mas-site/site/sitemap.xml;" in text
    assert nginx_rules("site", siterules.default_config()) == ""
    xml = sitemap_xml("example.com", [("/", None), ("/a&b", "2026-05-01")])
    assert "<loc>https://example.com/</loc>" in xml and "a&amp;b" in xml and "<lastmod>2026-05-01</lastmod>" in xml


def test_sitemap_lists_published_entries_only():
    cfg = clean({"sitemap": {"enabled": True, "pages": ["/"], "collections": {"articles": "/blog/{slug}"}}})
    cols = [{"id": "articles", "drafts": True, "sort_field": "date", "entries": [
        {"slug": "live", "date": "2026-05-01", "status": "published"}, {"slug": "wip", "status": "draft"},
        {"slug": "later", "status": "scheduled"}, {"slug": "../etc", "status": "published"}]}]
    assert collect_urls(cfg, cols) == [("/", None), ("/blog/live", "2026-05-01")]


def test_rules_are_written_into_the_nginx_site(env):
    client, db, host, pages = env
    h = {"x-csrf-token": login(client)}
    body = {"redirects": [{"from": "/old", "to": "/new", "code": 301}],
            "robots": {"enabled": True, "text": ""},
            "sitemap": {"enabled": True, "pages": ["/", "/contact"], "collections": {"articles": "/blog/{slug}"}}}
    r = client.put(f"{BASE}/seo/rules", json=body, headers=h)
    assert r.status_code == 200, r.text
    snippet = host.read_text("/etc/nginx/mas-maintenance/site.conf")
    assert "location = /old { return 301 /new; }" in snippet and "/etc/nginx/mas-site/site/sitemap.xml" in snippet
    assert "include /etc/nginx/mas-maintenance/site[.]conf;" in host.read_text(vhost_paths("site")[0])
    robots = host.read_text("/etc/nginx/mas-site/site/robots.txt")
    assert "Sitemap: https://client.example.com/sitemap.xml" in robots  # the default text points to the sitemap
    xml = host.read_text("/etc/nginx/mas-site/site/sitemap.xml")
    assert "https://client.example.com/contact" in xml
    got = client.get(f"{BASE}/seo", headers=h).json()
    assert got["rules"]["redirects"][0]["from"] == "/old" and got["collections"][0]["id"] == "articles"

    # turning everything off removes the snippet
    off = client.put(f"{BASE}/seo/rules", json={"redirects": [], "robots": {"enabled": False}, "sitemap": {"enabled": False}}, headers=h)
    assert off.status_code == 200
    assert host.read_text("/etc/nginx/mas-maintenance/site.conf") is None

    # refused input never reaches nginx
    bad = client.put(f"{BASE}/seo/rules", json={"redirects": [{"from": "/a;b", "to": "/c", "code": 301}]}, headers=h)
    assert bad.status_code == 400 and host.read_text("/etc/nginx/mas-maintenance/site.conf") is None


def test_nginx_refusal_keeps_the_previous_settings(env):
    client, db, host, pages = env
    h = {"x-csrf-token": login(client)}
    ok = {"redirects": [{"from": "/old", "to": "/new", "code": 301}]}
    assert client.put(f"{BASE}/seo/rules", json=ok, headers=h).status_code == 200
    host._commands["nginx -t"] = {"output": "emerg: boom", "returncode": 1}
    r = client.put(f"{BASE}/seo/rules", json={"redirects": [{"from": "/x", "to": "/y", "code": 301}]}, headers=h)
    assert r.status_code == 400
    assert siterules.clean(json.loads(db.get_setting("siterules:site")))["redirects"][0]["from"] == "/old"
    assert "location = /old" in host.read_text("/etc/nginx/mas-maintenance/site.conf")


def test_only_administrators_change_the_rules(env):
    client, db, host, pages = env
    uid = db.create_user("ed", auth.hash_password(PASSWORD), role="editor")
    db.set_user_apps(uid, ["site"])
    h = {"x-csrf-token": login(client, "ed")}
    assert client.put(f"{BASE}/seo/rules", json={}, headers=h).status_code in (401, 403)
    assert client.get(f"{BASE}/seo", headers=h).status_code == 200


def test_seo_audit_reads_the_live_site(env):
    client, db, host, pages = env
    h = {"x-csrf-token": login(client)}
    d = "https://client.example.com"
    pages[f"{d}/"] = html(f"{d}/", page("Home", "", extra="<img src=a.png>"))
    pages[f"{d}/robots.txt"] = html(f"{d}/robots.txt", "User-agent: *\nDisallow: /\n", ctype="text/plain")
    r = client.post(f"{BASE}/seo/audit", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    ids = {i["id"] for i in out["site"]} | {i["id"] for p in out["pages"] for i in p["issues"]}
    assert {"robots-blocks", "sitemap-missing", "title-short", "description-missing", "img-alt"} <= ids
    assert out["score"] < 70 and out["counts"]["error"] >= 2

    # a good site scores high
    good = page("A clear title for the home page", "x" * 80, extra='<link rel="canonical" href="/"><meta property="og:title" content="t"><meta property="og:image" content="i">')
    pages[f"{d}/"] = html(f"{d}/", good)
    pages[f"{d}/robots.txt"] = html(f"{d}/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {d}/sitemap.xml\n", ctype="text/plain")
    pages[f"{d}/sitemap.xml"] = html(f"{d}/sitemap.xml", f"<urlset><url><loc>{d}/</loc></url></urlset>", ctype="application/xml")
    db.set_setting("seo_audit:site", None)
    out = client.post(f"{BASE}/seo/audit", headers=h).json()
    assert out["score"] >= 90, out
    assert client.get(f"{BASE}/seo", headers=h).json()["audit"]["score"] == out["score"]


def test_suggestions_come_from_the_page():
    long_title = "A very long title about our bakery that search engines will cut in the middle of a sentence"
    body = ("<p>Short.</p><p>We bake sourdough bread every morning in the heart of Lyon. Come and taste it with our "
            "homemade jam, fresh butter and a hot coffee. Open every day.</p>")
    p = seo.read(f'<html lang="en"><head><title>{long_title}</title></head><body><nav><p>Menu menu menu menu menu menu menu menu menu menu</p></nav><h1>Bakery</h1>{body}</body></html>')
    s = seo.suggest(p, "Boulangerie Dupont")
    assert 50 <= len(s["description"]) <= 160 and s["description"].startswith("We bake sourdough bread") and "Menu" not in s["description"]
    assert len(s["title"]) <= 60 and not s["title"].endswith(" ")
    short = seo.read('<html><head><title>Home</title></head><body><h1>Our artisan bakery in Lyon</h1></body></html>')
    assert seo.suggest(short, "Dupont")["title"] == "Our artisan bakery in Lyon | Dupont"
    good = seo.read('<html><head><title>A clear title for the home page</title><meta name="description" content="%s"></head></html>' % ("x" * 80))
    assert seo.suggest(good) == {}


def test_optimizer_enables_robots_and_a_sitemap_with_guessed_patterns():
    cols = [{"id": "articles", "entries": [{"slug": "hello"}, {"slug": "second"}]}]
    cfg = siterules.default_config()
    new = siterules.optimized(cfg, ["robots", "sitemap"], "client.example.com",
                              ["/", "/contact", "/blog/hello", "/blog/second", "/about/"], cols)
    assert new["sitemap"]["enabled"] and new["sitemap"]["collections"] == {"articles": "/blog/{slug}"}
    assert "/contact" in new["sitemap"]["pages"] and "/blog/hello" not in new["sitemap"]["pages"]
    assert new["robots"]["enabled"] and "Sitemap: https://client.example.com/sitemap.xml" in new["robots"]["text"]
    clean(new)  # it passes the same checks as what a person types
    # a robots.txt that blocks everything is replaced, a custom one only gets its Sitemap line
    blocked = {**cfg, "robots": {"enabled": True, "text": "User-agent: *\nDisallow: /"}}
    assert "Disallow: /\n" not in siterules.optimized(blocked, ["robots"], "x.com", [], [])["robots"]["text"]
    custom = {**cfg, "robots": {"enabled": True, "text": "User-agent: *\nDisallow: /admin"}}
    got = siterules.optimized(custom, ["sitemap"], "x.com", [], [])["robots"]["text"]
    assert got.startswith("User-agent: *\nDisallow: /admin") and "Sitemap: https://x.com/sitemap.xml" in got


def test_optimize_endpoint_fixes_what_the_look_found(env):
    client, db, host, pages = env
    h = {"x-csrf-token": login(client)}
    d = "https://client.example.com"
    pages[f"{d}/"] = html(f"{d}/", page("A clear title for the home page", "x" * 80) .replace("</body>", '<a href="/contact">c</a><a href="/blog/hello">h</a></body>'))
    r = client.post(f"{BASE}/seo/audit", headers=h).json()
    assert {i["id"] for i in r["site"]} >= {"robots-missing", "sitemap-missing"} and "/blog/hello" in r["paths"]
    out = client.post(f"{BASE}/seo/optimize", json={}, headers=h)
    assert out.status_code == 200, out.text
    assert out.json()["applied"] == ["robots", "sitemap"]
    snippet = host.read_text("/etc/nginx/mas-maintenance/site.conf")
    assert "robots.txt" in snippet and "sitemap.xml" in snippet
    assert "/contact" in host.read_text("/etc/nginx/mas-site/site/sitemap.xml")
    assert client.post(f"{BASE}/seo/optimize", json={"fixes": ["nope"]}, headers=h).status_code == 400
    editor = db.create_user("ed", auth.hash_password(PASSWORD), role="editor"); db.set_user_apps(editor, ["site"])
    client.cookies.clear()
    eh = {"x-csrf-token": login(client, "ed")}
    assert client.post(f"{BASE}/seo/optimize", json={}, headers=eh).status_code in (401, 403)
