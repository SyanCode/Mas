"""Conversions: goals sent by the tracker script, joined to the visitors counted from the logs."""

from __future__ import annotations

import json
from datetime import date

import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.goals import record
from mas.host import FixtureHost
from mas.sitestats import SiteStats

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"
SITE = "https://shop.example.com"


def make(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com",
                  aliases=json.dumps(["www.shop.example.com"]))
    return host, db


def test_goals_are_counted_once_per_visitor_and_filtered(tmp_path):
    _, db = make(tmp_path)
    today = date(2026, 11, 12)
    assert record(db, SITE, "1.1.1.1", UA, "book", today)
    assert record(db, SITE, "1.1.1.1", UA, "book", today)  # a second click: a hit, not a visitor
    assert record(db, "https://www.shop.example.com", "1.1.1.1", UA, "tel", today)
    assert record(db, SITE, "2.2.2.2", UA, "book", today)
    # refused: unknown site, bots, bad names, the reserved total
    assert not record(db, "https://evil.example", "3.3.3.3", UA, "book", today)
    assert not record(db, SITE, "3.3.3.3", "Googlebot/2.1", "book", today)
    assert not record(db, SITE, "3.3.3.3", UA, "Book Now!", today)
    assert not record(db, SITE, "3.3.3.3", UA, "_any", today)
    rows = {r["goal"]: r for r in db.site_goals("shop", "2026-11-01")}
    assert (rows["book"]["hits"], rows["book"]["visitors"]) == (3, 2)
    assert (rows["tel"]["hits"], rows["tel"]["visitors"]) == (1, 1)
    assert (rows["_any"]["hits"], rows["_any"]["visitors"]) == (4, 2)  # one visitor, whatever they clicked
    for i in range(40):  # a site cannot invent unlimited goal names
        record(db, SITE, "4.4.4.4", UA, f"g{i}", today)
    assert len([r for r in db.site_goals("shop", "2026-11-01") if r["goal"] != "_any"]) == 30


def test_report_gives_conversion_rates(tmp_path):
    host, db = make(tmp_path)
    ua = lambda n: UA + f" {n}"  # noqa: E731
    lines = [f'10.0.0.{i} - - [12/Nov/2026:10:00:00 +0100] "GET / HTTP/2.0" 200 5 "-" "{ua(i)}"' for i in range(1, 11)]
    host.write_text("/var/log/nginx/mas-app-shop.access.log", "\n".join(lines) + "\n")
    for i in (1, 2, 3):
        record(db, SITE, f"10.0.0.{i}", ua(i), "book", date(2026, 11, 12))
    record(db, SITE, "10.0.0.1", ua(1), "tel", date(2026, 11, 12))
    report = SiteStats(host, Config(), db).report(db.get_app("shop"), days=7, today=date(2026, 11, 12))
    assert report["totals"]["visitors"] == 10
    assert report["conversion"] == {"visitors": 3, "rate": 30.0}
    assert report["goals"][0] == {"goal": "book", "hits": 3, "visitors": 3, "rate": 30.0}
    assert report["goals"][1]["goal"] == "tel" and report["days"][-1]["converted"] == 3


def test_script_and_public_beacon(tmp_path):
    host, db = make(tmp_path)
    client = TestClient(create_app(host, Config(cookie_secure=False), db, background=False))
    js = client.get("/t.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "/t/e" in js.text
    # cross-origin, no session, always 204 — even for garbage
    ok = client.post("/t/e", content=json.dumps({"g": "book"}), headers={"origin": SITE, "user-agent": UA,
                                                                          "content-type": "text/plain"})
    assert ok.status_code == 204
    assert client.post("/t/e", content="garbage", headers={"origin": SITE}).status_code == 204
    assert client.post("/t/e", content=json.dumps({"g": "x"}), headers={"origin": "https://evil.example"}).status_code == 204
    goals = db.site_goals("shop", "2000-01-01")
    assert [g["goal"] for g in goals] == ["_any", "book"]


def test_cookie_banner_config_script_and_decisions(tmp_path):
    from mas import cookies
    host, db = make(tmp_path)
    client = TestClient(create_app(host, Config(cookie_secure=False), db, background=False))
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    h = {"x-csrf-token": csrf}

    # off by default: the public config says nothing
    assert client.get("/t/c", params={"h": "shop.example.com"}).json() == {"enabled": False}
    js = client.get("/cookies.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "mas_consent" in js.text

    mine = client.get("/api/cms/sites/shop/cookies").json()
    assert mine["config"]["enabled"] is False and mine["stats"]["total"] == 0
    r = client.put("/api/cms/sites/shop/cookies", headers=h, json={
        "enabled": True, "position": "bottom", "accent": "#ff6600", "categories": ["analytics", "marketing"],
        "policy_url": "/privacy", "title": "Cookies", "expiry_months": 6})
    assert r.status_code == 200, r.text
    for bad in ({"accent": "red"}, {"position": "top"}, {"categories": ["tracking"]}, {"expiry_months": 24},
                {"policy_url": "javascript:alert(1)"}, {"message": "x" * 501}):
        assert client.put("/api/cms/sites/shop/cookies", headers=h, json=bad).status_code == 400
    public = client.get("/t/c", params={"h": "shop.example.com"})
    assert public.headers["access-control-allow-origin"] == "*"
    assert public.json()["accent"] == "#ff6600" and public.json()["categories"] == ["analytics", "marketing"]
    assert client.get("/t/c", params={"h": "evil.example"}).json() == {"enabled": False}  # unknown site

    for decision in ("accept", "accept", "reject", "custom", "bogus"):
        assert client.post("/t/k", content=json.dumps({"d": decision}),
                           headers={"origin": SITE, "content-type": "text/plain"}).status_code == 204
    stats = cookies.report(db, "shop")
    assert (stats["accept"], stats["reject"], stats["custom"], stats["accept_rate"]) == (2, 1, 1, 50.0)
    assert "ip" not in str(db.get_setting("cookies:shop"))
