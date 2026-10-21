"""Live state of a service: /proc, systemctl show and the Docker API."""

from __future__ import annotations

import server

from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost
from mas.runtime import CLK_TCK, _links, process_stats, service_runtime
from mas.state import Scanner


def stat(pid: int, ppid: int, ticks: int, start: int) -> str:
    # pid (comm) state ppid ... utime(14) stime(15) ... starttime(22)
    fields = ["S", str(ppid)] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 6 + [str(start)]
    return f"{pid} (node server) " + " ".join(fields) + "\n"


def test_process_tree_memory_and_systemd(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    svc = next(s for s in Scanner(host, Config(), db).refresh().services if s.id == "api.example.com")
    host.write_text("/proc/stat", "cpu 1 2 3\nbtime 1700000000\n")
    host.write_text("/proc/uptime", "1000.00 900.00\n")
    host.write_text(f"/proc/{svc.pid}/stat", stat(svc.pid, 1, 50, 10 * CLK_TCK))
    host.write_text(f"/proc/{svc.pid}/status", "Name:\tnode\nVmRSS:\t  40960 kB\nThreads:\t7\n")
    host.write_text("/proc/9999/stat", stat(9999, svc.pid, 5, 20 * CLK_TCK))  # a worker
    host.write_text("/proc/9999/status", "Name:\tnode\nVmRSS:\t  20480 kB\nThreads:\t3\n")

    p = process_stats(host, svc.pid, sample=0)
    assert p["processes"] == 2 and p["memory_mb"] == 60.0 and p["threads"] == 10
    assert p["started_at"] == "2023-11-14T22:13:30+00:00"  # btime + 10 s

    host._commands = {"systemctl show api.service *": {"output": (
        "Description=API\nUnitFileState=enabled\nNRestarts=3\nMemoryCurrent=73400320\nMemoryPeak=[not set]\n"
        "TasksCurrent=12\nCPUUsageNSec=4200000000\nActiveEnterTimestampMonotonic=400000000\nActiveState=active\n"
        "FragmentPath=/etc/systemd/system/api.service\n")}, **host._commands}
    rt = service_runtime(host, Config(public_ip="203.0.113.7"), db, svc, sample=0)
    sd = rt["systemd"]
    assert sd["enabled"] == "enabled" and sd["restarts"] == 3 and sd["memory_mb"] == 70.0
    assert sd["memory_peak_mb"] is None and sd["cpu_seconds"] == 4.2 and sd["tasks"] == 12
    assert rt["process"]["memory_mb"] == 60.0 and "pids" not in rt["process"]
    assert {"kind": "web", "url": "http://api.example.com/"} in rt["links"]
    assert {"kind": "web", "url": "http://app.example.com/api/"} in rt["links"]


def test_private_port_without_domain_gets_a_tunnel(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    svc = next(s for s in Scanner(host, Config(), db).refresh().services if s.id == "api.example.com")
    svc = svc.model_copy(update={"routes": [], "domains": [], "pid": None})
    links = _links(svc, [{"port": 8000, "address": "127.0.0.1", "public": False}], Config(public_ip="203.0.113.7"))
    assert links == [{"kind": "tunnel", "port": 8000, "url": "http://localhost:8000",
                      "command": "ssh -L 8000:127.0.0.1:8000 <you>@203.0.113.7"}]
    links = _links(svc, [{"port": 25565, "address": "0.0.0.0", "public": True}], Config())
    assert links == [{"kind": "port", "port": 25565, "url": "http://<server>:25565"}]


def test_the_site_of_a_mas_app_is_linked_to_it(tmp_path):
    from mas.models import Manager, Service
    from mas.runtime import app_for
    apps = [{"id": "bb", "name": "bb", "mode": "managed", "port": None, "service_id": None}]
    site = Service(id="bb.example.com", name="bb.example.com", manager=Manager.NGINX_STATIC,
                   path="/srv/mas/apps/bb/current/dist", nginx_files=["/etc/nginx/sites-enabled/mas-app-bb.conf"])
    assert app_for(site, apps, "/srv/mas/apps") == {"id": "bb", "name": "bb"}
    other = site.model_copy(update={"path": "/var/www/bb", "nginx_files": ["/etc/nginx/sites-enabled/bb.conf"]})
    assert app_for(other, apps, "/srv/mas/apps") is None
    assert app_for(other.model_copy(update={"path": "/srv/mas/apps/bbb/current"}), apps, "/srv/mas/apps") is None
