from __future__ import annotations

import server

from mas.collectors import ScanContext, docker, pm2, ports, processes, systemd
from mas.collectors.processes import UserEntry, parse_cgroup
from mas.config import Config
from mas.host import FixtureHost

# -- ports -----------------------------------------------------------------


def test_parse_ss_tcp_merges_ipv4_ipv6_and_strips_interface():
    out = ports.parse_ss_tcp(
        'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=11,fd=6),("nginx",pid=10,fd=6))\n'
        'LISTEN 0 511 [::]:80 [::]:* users:(("nginx",pid=11,fd=7),("nginx",pid=10,fd=7))\n'
        'LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=5,fd=14))\n'
        "LISTEN 0 128 [::ffff:127.0.0.1]:7000 [::]:*\n"
    )
    assert [(s.port, s.address, s.process, s.pid) for s in out] == [
        (53, "127.0.0.53", "systemd-resolve", 5),
        (80, "0.0.0.0", "nginx", 10),
        (7000, "127.0.0.1", None, None),
    ]


def test_socket_activation_pid1_is_ignored():
    (sock,) = ports.parse_ss_tcp(
        'LISTEN 0 80 127.0.0.1:3306 0.0.0.0:* users:(("systemd",pid=1,fd=50),("mariadbd",pid=2213,fd=20))\n'
    )
    assert (sock.process, sock.pid) == ("mariadbd", 2213)
    (alone,) = ports.parse_ss_tcp('LISTEN 0 80 0.0.0.0:631 0.0.0.0:* users:(("systemd",pid=1,fd=9))\n')
    assert (alone.process, alone.pid) == ("systemd", 1)


def test_parse_ss_unix():
    (sock,) = ports.parse_ss_unix(
        'u_str LISTEN 0 511 /run/php/php8.2-fpm.sock 21345 * 0 users:(("php-fpm8.2",pid=900,fd=8))\n'
        "u_str LISTEN 0 4096 @/tmp/.X11-unix/X0 1 * 0\n"
    )
    assert (sock.address, sock.pid) == ("/run/php/php8.2-fpm.sock", 900)


def test_ss_missing(tmp_path):
    ctx = ScanContext(FixtureHost(tmp_path))
    assert ports.collect(ctx) == []
    assert "ss" in ctx.warnings[0]


# -- processes -------------------------------------------------------------


def test_parse_cgroup():
    assert parse_cgroup("0::/system.slice/api.service\n")[1:] == ("api.service", None)
    cid = "ab" * 32
    assert parse_cgroup(f"0::/system.slice/docker-{cid}.scope\n")[2] == cid
    assert parse_cgroup("12:pids:/x\n1:name=systemd:/system.slice/cron.service\n")[1] == "cron.service"
    assert parse_cgroup("0::/user.slice/user-1000.slice/user@1000.service/app.slice/x.service\n")[1] is None


def test_read_process(ctx):
    users = processes.load_users(ctx.host)
    p = processes.read_process(ctx, 2000, users)
    assert p.user == "alice" and p.cwd == "/home/alice/apps/web"
    assert p.cmdline[0] == "node" and p.pm2_app == "web"
    assert p.systemd_unit == "pm2-alice.service"
    assert processes.read_process(ctx, 424242, users) is None


# -- systemd ---------------------------------------------------------------


def test_systemd_filters_system_units(ctx):
    units = {u.name: u for u in systemd.collect(ctx)}
    assert set(units) == {"api.service", "bot.service", "colord.service", "mariadb.service",
                          "prometheus-node-exporter.service"}
    api = units["api.service"]
    assert api.main_pid == 3000 and api.working_directory == "/srv/api" and api.user == "api"
    assert api.exec_start.startswith("/srv/api/.venv/bin/gunicorn")


def test_systemd_exclusion_is_configurable(host):
    ctx = ScanContext(host, Config(excluded_units=["mariadb", "prometheus-*"]))
    names = {u.name for u in systemd.collect(ctx)}
    assert "mariadb.service" not in names and "ssh.service" in names


def test_systemd_text_fallback():
    out = systemd.parse_list_text(
        "api.service   loaded active running Acme API\ncron.service loaded active running Cron\n"
    )
    assert [u.name for u in out] == ["api.service", "cron.service"]
    assert out[0].description == "Acme API"


# -- pm2 -------------------------------------------------------------------


def test_pm2_jlist(ctx):
    users = processes.load_users(ctx.host)
    apps = {a.name: a for a in pm2.collect(ctx, users)}
    assert set(apps) == {"web", "worker", "report"}
    assert apps["web"].pid == 2000 and apps["web"].port == 3000 and apps["web"].status == "running"
    assert apps["report"].status == "stopped" and apps["report"].pid is None
    # queried as the owner, never as root
    assert any(cmd[:3] == ["runuser", "-u", "alice"] for cmd in ctx.host.executed)


def test_pm2_jlist_with_leading_warning():
    user = UserEntry("bob", 1001, "/home/bob")
    out = '>>>> In-memory PM2 is out-of-date\n[{"name":"x","pm_id":0,"pid":5,"pm2_env":{"status":"errored"}}]'
    (app,) = pm2.parse_jlist(out, user)
    assert app.status == "errored"


def test_pm2_not_queried_when_daemon_down(tmp_path):
    dest = server.build(tmp_path / "srv")
    (dest / "root/home/alice/.pm2/pm2.pid").unlink()
    ctx = ScanContext(FixtureHost(dest))
    assert pm2.collect(ctx, processes.load_users(ctx.host)) == []
    assert not any("jlist" in " ".join(c) for c in ctx.host.executed)
    assert any("daemon not running" in w for w in ctx.warnings)


# -- docker ----------------------------------------------------------------


def test_docker_containers(ctx):
    containers = {c.name: c for c in docker.collect(ctx)}
    grafana = containers["monitoring-grafana-1"]
    assert grafana.public_ports == [2025, 3001] and grafana.working_dir == "/opt/monitoring"
    assert containers["old-redis"].status == "stopped"


def test_docker_absent(tmp_path):
    ctx = ScanContext(FixtureHost(server.build(tmp_path / "srv", docker=False)))
    assert docker.collect(ctx) == []
    assert any("docker" in w for w in ctx.warnings)
