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
    group(host, f"docker-{'d' * 64}.scope", 4600, 0)
    host.write_text(f"{SLICE}/docker-{'d' * 64}.scope/memory.stat", f"anon 1\ninactive_file {600 * 1048576}\n")
    running = [{"Id": "d" * 64, "Names": ["/sxnpaf-mc-1"], "Image": "itzg/minecraft-server",
                "Labels": {"com.docker.compose.project": "sxnpaf"}},
               {"Id": "a" * 64, "Names": ["/mas-mail-mail-1"], "Labels": {"com.docker.compose.project": "mas-mail"}},
               {"Id": "b" * 64, "Names": ["/mas-supabase-db"], "Labels": {"com.docker.compose.project": "mas-supabase"}}]
    real = host.docker_get
    mc = {"Config": {"Env": ["EULA=TRUE", "MEMORY=4G"]}}
    host.docker_get = lambda path: running if path == "/containers/json" else mc if "dddd" in path else real(path)
    scanner = Scanner(host, config, db)
    resources = Resources(host, config, db, deployer, Cluster(host, config, db, deployer))
    u = Usage(host, config, db, scanner, resources)
    first = u.overview()
    rows = {r["key"]: r for r in first["rows"]}
    assert [r["key"] for r in first["rows"]][:3] == ["project:sxnpaf", "unit:mariadb.service", "app:site"]
    assert rows["app:site"]["memory_mb"] == 350  # its two instances
    assert sorted(rows["app:site"]["parts"]) == ["mas-app-site.2.service", "mas-app-site.service"]
    assert rows["app:site"]["limit_mb"] == 512 and rows["app:site"]["instances"] == 2
    assert rows["unit:mariadb.service"]["limit_mb"] == 2048
    assert rows["app:mail"]["kind"] == "stack" and rows["app:mail"]["memory_mb"] == 300
    assert rows["unit:postgresql@15-main.service"]["memory_mb"] == 80
    assert rows["supabase"]["memory_mb"] == 100
    assert rows["project:sxnpaf"]["memory_mb"] == 4000  # without the file cache
    assert rows["project:sxnpaf"]["advice"][0]["id"] == "minecraft-heap" and rows["mas"]["memory_mb"] == 60
    # CPU from the previous reading: mariadb used 0.5 s more
    group(host, "mariadb.service", 900, 505000)
    u._last["mariadb.service"] = (u._last["mariadb.service"][0] - 1.0, 5000)
    second = {r["key"]: r for r in u.overview()["rows"]}
    assert 40 <= second["unit:mariadb.service"]["cpu_percent"] <= 51


def test_heavy_containers_get_a_limit_and_clamav_is_pointed_at(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_mod, "FIRST_SAMPLE", 0)
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config()
    deployer = Deployer(host, config, db, lambda: None)
    host.write_text("/proc/meminfo", "MemTotal: 4096000 kB\nMemAvailable: 2048000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    group(host, f"docker-{'e' * 64}.scope", 1650, 0)
    group(host, f"docker-{'f' * 64}.scope", 800, 0)
    group(host, f"docker-{'c' * 64}.scope", 100, 0)
    running = [{"Id": "e" * 64, "Names": ["/sxnmail"], "Image": "mailserver/docker-mailserver"},
               {"Id": "f" * 64, "Names": ["/capped"], "Image": "redis"},
               {"Id": "c" * 64, "Names": ["/small"], "Image": "redis"}]
    inspect = {"e": {"Config": {"Env": ["ENABLE_CLAMAV=1"]}, "HostConfig": {"Memory": 0}},
               "f": {"Config": {"Env": []}, "HostConfig": {"Memory": 1024 * 1048576}}}
    calls = []
    host.docker_get = lambda path: running if path.startswith("/containers/json") else calls.append(path) or inspect[path.split("/")[2][0]]
    resources = Resources(host, config, db, deployer, Cluster(host, config, db, deployer))
    u = Usage(host, config, db, Scanner(host, config, db), resources)
    rows = {r["key"]: r for r in u.overview()["rows"]}
    advice = {a["id"]: a for a in rows["container:sxnmail"]["advice"]}
    assert advice["container-limit"]["set"] == {"container_memory_mb": 2496, "container": "sxnmail"}
    assert "container-clamav" in advice
    assert rows["container:capped"]["advice"] == [] and rows["container:small"]["advice"] == []
    u.overview()
    assert len(calls) == 2  # inspected once, then from the cache
    # the economy mode caps it only once the person agreed to it
    host._commands = {"docker update --memory 2496m --memory-swap 2496m sxnmail": {"output": "sxnmail"}, **host._commands}
    resources.containers = u.heavy_containers
    resources.set_auto(True)
    resources.auto_tune(10_000, lambda app_id: None)
    assert ["docker", "update", "--memory", "2496m", "--memory-swap", "2496m", "sxnmail"] not in host.executed
    resources.set_auto(True, shrink=True)
    assert resources.auto_tune(10_000 + 7200, lambda app_id: None) == ["container sxnmail: memory limit 2496 MB"]
    assert ["docker", "update", "--memory", "2496m", "--memory-swap", "2496m", "sxnmail"] in host.executed
