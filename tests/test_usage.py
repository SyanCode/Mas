"""The Resources page: memory and CPU of everything, from the cgroups."""

from __future__ import annotations

import server

from mas import usage as usage_mod
from mas.cluster import Cluster
from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer
from mas.host import FixtureHost
from mas.resources import Resources
from mas.state import Scanner
from mas.usage import SLICE, Usage


def group(host, name, mb, usec, limit="max"):
    host.write_text(f"{SLICE}/{name}/memory.current", str(mb * 1048576))
    host.write_text(f"{SLICE}/{name}/memory.max", str(limit if limit == "max" else limit * 1048576))
    host.write_text(f"{SLICE}/{name}/cpu.stat", f"usage_usec {usec}\nuser_usec 1\n")


def test_everything_grouped_and_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_mod, "FIRST_SAMPLE", 0)
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config()
    deployer = Deployer(host, config, db, lambda: None)
    db.create_app("site", name="Site", repo="acme/site", branch="main", type="node", port=3100)
    db.update_app("site", mode="managed", extra_ports="[3101]", resources='{"memory_mb": 512}')
    db.create_app("mail", name="Mail", repo="", branch="", type="node", port=None)
    db.update_app("mail", mode="stack")
    group(host, "mas-app-site.service", 200, 1000)
    group(host, "mas-app-site.2.service", 150, 1000)
    group(host, "mariadb.service", 900, 5000, limit=2048)
    group(host, "mas.service", 60, 100)
    group(host, "system-postgresql.slice/postgresql@15-main.service", 80, 100)
    group(host, f"docker-{'a' * 64}.scope", 300, 0)
    group(host, f"docker-{'b' * 64}.scope", 100, 0)
    host.write_text("/proc/meminfo", "MemTotal: 4096000 kB\nMemAvailable: 2048000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    running = [{"Id": "a" * 64, "Names": ["/mas-mail-mail-1"], "Labels": {"com.docker.compose.project": "mas-mail"}},
               {"Id": "b" * 64, "Names": ["/mas-supabase-db"], "Labels": {"com.docker.compose.project": "mas-supabase"}}]
    real = host.docker_get
    host.docker_get = lambda path: running if path == "/containers/json" else real(path)
    scanner = Scanner(host, config, db)
    resources = Resources(host, config, db, deployer, Cluster(host, config, db, deployer))
    u = Usage(host, config, db, scanner, resources)
    first = u.overview()
    rows = {r["key"]: r for r in first["rows"]}
    assert [r["key"] for r in first["rows"]][:2] == ["unit:mariadb.service", "app:site"]
    assert rows["app:site"]["memory_mb"] == 350  # its two instances
    assert sorted(rows["app:site"]["parts"]) == ["mas-app-site.2.service", "mas-app-site.service"]
    assert rows["app:site"]["limit_mb"] == 512 and rows["app:site"]["instances"] == 2
    assert rows["unit:mariadb.service"]["limit_mb"] == 2048
    assert rows["app:mail"]["kind"] == "stack" and rows["app:mail"]["memory_mb"] == 300
    assert rows["unit:postgresql@15-main.service"]["memory_mb"] == 80
    assert rows["supabase"]["memory_mb"] == 100 and rows["mas"]["memory_mb"] == 60
    # CPU from the previous reading: mariadb used 0.5 s more
    group(host, "mariadb.service", 900, 505000)
    u._last["mariadb.service"] = (u._last["mariadb.service"][0] - 1.0, 5000)
    second = {r["key"]: r for r in u.overview()["rows"]}
    assert 40 <= second["unit:mariadb.service"]["cpu_percent"] <= 51
