"""Alerts about the server: disk, certificates, nginx. Said once when they appear, once when they are gone."""

from __future__ import annotations

from collections import namedtuple

from mas.health import Check
from tests.test_notify import make

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3


def setup(tmp_path):
    client, db, n, hooks, h = make(tmp_path)
    alerts = client.app.state.host_alerts
    state = {"used": 50, "checks": []}
    alerts.disk_usage = lambda path: Usage(100 * GB, state["used"] * GB, (100 - state["used"]) * GB)
    alerts.checks = lambda: state["checks"]
    r = client.put("/api/host-alerts", headers=h, json={"enabled": True, "emails": ["me@example.com"],
                                                        "webhook": "https://hooks.slack.com/services/T/B/X", "disk_percent": 90})
    assert r.status_code == 200 and r.json()["has_webhook"] and r.json()["webhook"] == ""
    return client, db, alerts, state, hooks, h


def test_settings_are_validated_and_sealed(tmp_path):
    client, db, alerts, state, hooks, h = setup(tmp_path)
    assert "hooks.slack.com" not in db.get_setting("hostalerts")
    assert client.put("/api/host-alerts", headers=h, json={"emails": ["nope"]}).status_code == 400
    assert client.put("/api/host-alerts", headers=h, json={"emails": [], "disk_percent": 10}).status_code == 400
    assert client.put("/api/host-alerts", headers=h, json={"emails": [], "webhook": "http://insecure"}).status_code == 400


def test_announces_once_then_resolution(tmp_path):
    client, db, alerts, state, hooks, h = setup(tmp_path)
    assert alerts.tick(now=1000, force=True) == []  # all fine: silence
    state["used"] = 95
    assert alerts.tick(now=2000, force=True)
    assert len(hooks) == 1 and "95%" in hooks[0][1]["message"]
    assert alerts.tick(now=3000, force=True) == []  # still there: not repeated
    assert len(hooks) == 1
    state["used"] = 40
    assert alerts.tick(now=4000, force=True)
    assert len(hooks) == 2 and hooks[1][1]["title"].endswith("Server back to normal")


def test_certificate_errors_and_throttle(tmp_path):
    client, db, alerts, state, hooks, h = setup(tmp_path)
    state["checks"] = [Check("error", "certificates", "Certificate of shop.example.com expired", "since 3 days"),
                       Check("warning", "memory", "No swap")]
    assert len(alerts.tick(now=1000, force=True)) == 1  # warnings do not wake anybody
    assert alerts.tick(now=1100) == []  # looked at less than 5 minutes ago


def test_off_by_default_and_api_tokens_excluded(tmp_path):
    client, db, n, hooks, h = make(tmp_path)
    assert client.get("/api/host-alerts").json()["enabled"] is False
    assert client.app.state.host_alerts.tick(force=True) == []
    from mas import auth
    assert not auth.api_token_allowed("/api/host-alerts")
