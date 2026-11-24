"""Resources of the apps: sleep when idle, wake-up, instances that follow the traffic, advice."""

from __future__ import annotations

import json

import pytest
import server

from mas.cluster import Cluster
from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer
from mas.host import FixtureHost
from mas.resources import ResourceError, Resources, validate_resources


def test_validation():
    assert validate_resources(512, None, None) == {"memory_mb": 512}
    assert validate_resources(None, 30, {"min": 2, "max": 4}) == {"sleep_after_min": 30,
                                                                  "autoscale": {"min": 1, "max": 4}}
    assert validate_resources(0, 0, {"min": 2, "max": 2}) == {}  # nothing to scale
    for bad in ((32, None, None), (None, 2, None), (None, None, {"min": 3, "max": 2})):
        with pytest.raises(ResourceError):
            validate_resources(*bad)


@pytest.fixture
def res(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config(secret_key_path="/etc/mas/secret.key")
    deployer = Deployer(host, config, db, lambda: None)
    scaled = []
    deployer.scale = lambda app_id, n, rlog: scaled.append((app_id, n)) or db.update_app(
        app_id, extra_ports=json.dumps(list(range(7101, 7100 + n))))
    cluster = Cluster(host, config, db, deployer)
    r = Resources(host, config, db, deployer, cluster)
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100, mode="managed",
                  domain="web.example.com")
    return r, db, host, scaled


def visits(host, at: float, count: int, addr: str = "127.0.0.1:7100"):
    lines = "".join(f"{at - i * 0.01:.3f} {addr} 200 0.010\n" for i in range(count))
    path = host.real("/var/log/nginx/mas-app-web.upstream.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(lines)


def site(host, wake: bool):
    path = host.real("/etc/nginx/sites-available/mas-app-web.conf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("server {\n    error_page 502 503 504 = @mas_wake;\n}\n" if wake else "server {}\n")


def test_no_sleep_without_the_wake_up_in_nginx(res):
    """nginx refused the wake-up (or the site predates it): stopping the app would take the site down."""
    r, db, host, _ = res
    db.update_app("web", resources=json.dumps({"sleep_after_min": 30}))
    site(host, wake=False)
    r.started = 1000.0
    assert r.tick(now=1000.0 + 31 * 60) == [] and not r.asleep("web")
    assert ["systemctl", "stop", "mas-app-web.service"] not in host.executed


def test_sleep_when_idle_and_wake_on_a_visit(res):
    r, db, host, _ = res
    db.update_app("web", resources=json.dumps({"sleep_after_min": 30}))
    site(host, wake=True)
    r.started = 1000.0
    visits(host, 1000.0, 3)
    assert r.tick(now=1000.0 + 600) == []  # 10 minutes: still awake
    notes = r.tick(now=1000.0 + 31 * 60)
    assert "asleep after 30 min" in notes[0] and r.asleep("web")
    assert ["systemctl", "stop", "mas-app-web.service"] in host.executed
    assert r.tick(now=1000.0 + 40 * 60) == []  # asleep: nothing more
    assert r.wake("web") and not r.asleep("web")
    assert ["systemctl", "start", "mas-app-web.service"] in host.executed
    assert not r.wake("web")  # awake: nothing to do


def test_instances_follow_the_traffic(res):
    r, db, host, scaled = res
    db.update_app("web", resources=json.dumps({"autoscale": {"min": 1, "max": 3}}))
    now = 50_000.0
    visits(host, now, 1500)  # 1500 req/min: 3 instances wanted
    r.tick(now=now)
    assert scaled == [("web", 2)]  # one at a time
    r.tick(now=now + 30)
    assert scaled == [("web", 2)]  # cooldown
    visits(host, now + 130, 1500)
    r.tick(now=now + 130)
    assert scaled[-1] == ("web", 3)
    # calm: down only after 10 quiet minutes
    r.tick(now=now + 130 + 2000)
    assert scaled[-1] == ("web", 3)
    r.tick(now=now + 130 + 2000 + 601)
    assert scaled[-1] == ("web", 2)


def test_advice(res):
    r, db, host, _ = res
    r.started = 0
    host._commands = {"systemctl show mas-app-web.service -pMemoryCurrent --value": {"output": "314572800\n"},
                      **host._commands}
    ids = {a["id"]: a for a in r.advice(db.get_app("web"))}
    assert ids["memory-limit"]["set"] == {"memory_mb": 512} and ids["memory-limit"]["params"]["used"] == 300
    assert ids["sleep"]["set"] == {"sleep_after_min": 30}
    db.update_app("web", resources=json.dumps({"memory_mb": 320, "sleep_after_min": 30}))
    ids = {a["id"]: a for a in r.advice(db.get_app("web"))}
    assert "memory-high" in ids and "sleep" not in ids


def test_economy_mode_applies_the_safe_advice_by_itself(res):
    r, db, host, _ = res
    r.started = 0
    host._commands = {"systemctl show mas-app-web.service -pMemoryCurrent --value": {"output": "314572800\n"},
                      **host._commands}
    applied = []
    assert r.auto_tune(10_000, applied.append) == []  # off by default
    r.set_auto(True)
    out = r.auto_tune(10_000, applied.append)
    # a limit with a wide margin at once, but sleep only after three quiet looks in a row
    assert json.loads(db.get_app("web")["resources"]) == {"memory_mb": 640} and applied == ["web"]
    assert out == ["web: memory limit 640 MB (uses 300)"]
    assert r.auto_tune(10_100, applied.append) == []  # not before two hours
    r.auto_tune(10_000 + 7200, applied.append)
    assert "sleep_after_min" not in json.loads(db.get_app("web")["resources"])
    r.auto_tune(10_000 + 2 * 7200, applied.append)
    assert json.loads(db.get_app("web")["resources"]) == {"memory_mb": 640, "sleep_after_min": 30}
    # a visit resets the count; a value set by a person is never lowered
    db.update_app("web", resources=json.dumps({"memory_mb": 4096}))
    db.set_setting("resources_auto_quiet:web", "2")
    visits(host, 10_000 + 3 * 7200, 200)
    r.auto_tune(10_000 + 3 * 7200, applied.append)
    assert json.loads(db.get_app("web")["resources"]) == {"memory_mb": 4096}
    assert db.get_setting("resources_auto_quiet:web") is None
    assert any(h["action"] == "resources-auto" for h in db.history(limit=10))


def test_a_limit_far_too_wide_is_lowered_only_with_consent(res):
    r, db, host, _ = res
    r.started = 0
    host._commands = {"systemctl show mas-app-web.service -pMemoryCurrent --value": {"output": str(454 * 1048576)},
                      "systemctl show mas-app-web.service -pMemoryPeak --value": {"output": str(700 * 1048576)},
                      **host._commands}
    db.update_app("web", resources=json.dumps({"memory_mb": 5056}))
    ids = {a["id"]: a for a in r.advice(db.get_app("web"))}
    assert ids["memory-oversized"]["set"] == {"memory_mb": 1408} and ids["memory-oversized"]["params"]["peak"] == 700
    r.set_auto(True)
    r.auto_tune(10_000, lambda app_id: None)
    assert json.loads(db.get_app("web")["resources"])["memory_mb"] == 5056  # a person's value, no consent yet
    r.set_auto(True, shrink=True)
    r.auto_tune(10_000 + 7200, lambda app_id: None)
    assert json.loads(db.get_app("web")["resources"])["memory_mb"] == 1408
    db.update_app("web", resources=json.dumps({"memory_mb": 2048}))  # under four times the peak: left alone
    assert "memory-oversized" not in {a["id"] for a in r.advice(db.get_app("web"))}


def test_the_uptime_checks_of_mas_do_not_keep_an_app_awake(res):
    from mas.uptime import Uptime
    r, db, host, _ = res
    db.update_app("web", resources=json.dumps({"sleep_after_min": 30}))
    site(host, wake=True)
    r.started = 1000.0
    up = Uptime(db, fetch=lambda url, follow=True: __import__("mas.compliance", fromlist=["Response"]).Response(url, 200))
    r.uptime = up
    # a check every minute for half an hour: each one leaves a line in the upstream log, like a visitor would
    for minute in range(1, 32):
        at = 1000.0 + minute * 60
        up.windows["web"] = [*up.windows.get("web", [])[-39:], (at - 0.2, at + 0.1)]
        visits(host, at, 1)
        r.tick(now=at + 1)
    assert r.asleep("web")  # those were not visits
    # asleep: the checks stop opening it (which would wake it up), a real visitor still does
    calls = []
    up.fetch = lambda url, follow=True: calls.append(url)
    assert up.check(db.get_app("web")) == {"ok": True, "skipped": "asleep"} and calls == []
