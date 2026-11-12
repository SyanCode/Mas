"""Uptime monitoring: checks, incidents with two failures in a row, notifications and the public page."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.compliance import Response
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost
from mas.uptime import Uptime

T0 = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc).timestamp()


def make(tmp_path, statuses):
    db = Database(tmp_path / "mas.db")
    db.create_app("shop", name="Shop <b>", repo="acme/shop", branch="main", type="static", domain="shop.example.com")
    sent = []

    class Notifier:
        def notify(self, app_id, event, title, text):
            sent.append((event, title, text))
            return []
    queue = list(statuses)
    up = Uptime(db, fetch=lambda url, follow=True: queue.pop(0), notifier=Notifier())
    return db, up, sent


def test_two_failures_open_an_incident_and_a_good_answer_closes_it(tmp_path):
    ok, bad = Response("u", 200), Response("u", 502)
    db, up, sent = make(tmp_path, [ok, bad, bad, bad, ok, ok])
    app = db.get_app("shop")
    for i in range(6):
        assert up.tick(T0 + i * 60) == ["shop"]
    assert [m[1] for m in sent] == ["Site down", "Back online"]  # the single blip at minute 1 was not an incident
    assert "HTTP 502" in sent[0][2] and "minute" in sent[1][2]
    r = up.report("shop", 7, T0 + 400)
    assert r["checks"] == 6 and r["uptime"] == 50.0 and r["up_now"] is True and len(r["incidents"]) == 1
    assert r["incidents"][0]["ended"] and r["incidents"][0]["reason"] == "HTTP 502"
    assert up.tick(T0 + 5 * 60 + 10) == []  # checked less than a minute ago
    assert app["id"] == "shop"


def test_tls_and_network_failures_name_their_reason(tmp_path):
    db, up, sent = make(tmp_path, [Response("u", 0, error="tls"), Response("u", 0, error="tls")])
    up.tick(T0)
    up.tick(T0 + 60)
    assert "invalid HTTPS certificate" in sent[0][2]
    assert db.open_incident("shop") is not None


def test_a_404_is_a_working_server(tmp_path):
    db, up, sent = make(tmp_path, [Response("u", 404), Response("u", 404)])
    up.tick(T0); up.tick(T0 + 60)
    assert sent == [] and up.report("shop", 1, T0 + 100)["uptime"] == 100.0


def test_public_status_page_is_opt_in_and_escaped(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    db.create_app("shop", name="Shop <b>", repo="acme/shop", branch="main", type="static", domain="shop.example.com", installation_id=1)
    api = create_app(host, Config(cookie_secure=False), db, background=False)
    client = TestClient(api)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    h = {"x-csrf-token": csrf}
    assert client.get("/status/shop").status_code == 404  # off by default
    assert client.get("/status/nope").status_code == 404
    api.state.uptime.fetch = lambda url, follow=True: Response(url, 200)
    api.state.uptime.tick()
    assert client.put("/api/cms/sites/shop/uptime", headers=h, json={"status_page": True}).json() == {"status_page": True}
    page = client.get("/status/shop")
    assert page.status_code == 200 and "All systems operational" in page.text
    assert "<b>" not in page.text and "Shop &lt;b&gt;" in page.text  # names are escaped
    mine = client.get("/api/cms/sites/shop/uptime").json()
    assert mine["status_page"] is True and mine["up_now"] is True and mine["checks"] == 1
    assert client.put("/api/cms/sites/shop/uptime", headers=h, json={"status_page": False}).status_code == 200
    assert client.get("/status/shop").status_code == 404
    assert json.dumps(mine)
