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


def test_sleep_when_idle_and_wake_on_a_visit(res):
    r, db, host, _ = res
    db.update_app("web", resources=json.dumps({"sleep_after_min": 30}))
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
