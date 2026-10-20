"""End-to-end scan of the fixture server."""

from __future__ import annotations

import hashlib
import json

import server
from typer.testing import CliRunner

from mas.cli import app
from mas.correlator import scan
from mas.host import FixtureHost
from mas.models import Manager, ServiceType, Status

READ_ONLY_COMMANDS = {("ss", "-tlnpH"), ("ss", "-xlnpH"), ("systemctl", "list-units"),
                      ("systemctl", "show"), ("runuser", "-u")}


def test_proxy_vhost_to_pm2_nextjs(services):
    app_ = services["app.example.com"]
    assert app_.manager == Manager.PM2
    assert app_.type == ServiceType.NEXTJS
    assert (app_.port, app_.pid, app_.user) == (3000, 2000, "alice")  # pid 2001 is a child of the pm2 app
    assert app_.path == "/home/alice/apps/web"
    assert app_.status == Status.RUNNING
    assert app_.git_remote == "git@github.com:acme/web.git" and app_.git_branch == "production"
    assert app_.ssl_expires == server.APP_CERT_EXPIRES
    assert app_.https_redirect


def test_proxy_vhost_to_systemd_python(services):
    api = services["api.example.com"]
    assert api.manager == Manager.SYSTEMD and api.unit == "api.service"
    assert api.type == ServiceType.PYTHON
    assert (api.port, api.pid, api.user, api.path) == (8000, 3000, "api", "/srv/api")
    # app.example.com/api/ reaches the same backend through an upstream
    assert api.routes == ["api.example.com/", "app.example.com/api/"]


def test_php_vhost(services):
    blog = services["blog.example.com"]
    assert blog.manager == Manager.PHP_FPM and blog.type == ServiceType.PHP
    assert blog.path == "/var/www/blog/public"
    assert blog.status == Status.RUNNING and blog.unit == "php8.2-fpm.service"
    assert blog.domains == ["blog.example.com", "www.blog.example.com"]
    assert blog.git_remote == "https://github.com/acme/blog.git"  # token stripped
    assert 0 <= (blog.ssl_expires - __import__("datetime").datetime.now(blog.ssl_expires.tzinfo)).days < 14


def test_static_vhost_with_several_names(services):
    docs = services["docs.example.com"]
    assert docs.manager == Manager.NGINX_STATIC and docs.type == ServiceType.STATIC
    assert docs.domains == ["docs.example.com", "doc.example.com"]
    assert docs.status == Status.RUNNING


def test_docker_behind_upstream(services):
    g = services["grafana.example.com"]
    assert g.manager == Manager.DOCKER and g.unit == "monitoring-grafana-1"
    assert g.port == 3001 and g.path == "/opt/monitoring"


def test_vhost_whose_app_is_down(services):
    shop = services["shop.example.com"]
    assert shop.status == Status.STOPPED and shop.port == 4000


def test_services_without_domain(services):
    no_domain = {s.id: s for s in services.values() if not s.domains}
    assert no_domain["unknown-python3"].port == 5055
    assert no_domain["unknown-python3"].manager == Manager.UNKNOWN
    assert no_domain["systemd-mariadb"].port == 3306
    assert no_domain["systemd-prometheus-node-exporter"].port == 9100
    assert no_domain["pm2-worker"].type == ServiceType.NODE
    assert no_domain["pm2-report"].status == Status.STOPPED
    assert no_domain["docker-old-redis"].status == Status.STOPPED
    assert no_domain["nginx-default"].type == ServiceType.STATIC


def test_system_services_are_excluded(services):
    units = {s.unit for s in services.values()}
    names = {s.name for s in services.values()}
    assert not units & {"ssh.service", "pm2-alice.service", "cron.service", "systemd-journald.service"}
    assert not names & {"sshd", "systemd-resolve", "nginx", "docker-proxy"}


def test_each_backend_appears_once(result):
    ids = [s.id for s in result.services]
    assert len(ids) == len(set(ids)) == 13


def test_docker_absent_does_not_break_scan(tmp_path):
    res = scan(FixtureHost(server.build(tmp_path / "srv", docker=False)))
    assert any(w.startswith("docker:") for w in res.warnings)
    assert all(s.manager != Manager.DOCKER for s in res.services)
    assert "app.example.com" in {s.id for s in res.services}


def test_manual_type_override(server_dir):
    res = scan(FixtureHost(server_dir), type_overrides={"shop.example.com": ServiceType.PHP})
    shop = next(s for s in res.services if s.id == "shop.example.com")
    assert shop.type == ServiceType.PHP and shop.type_source == "manual"


def test_scan_is_read_only(tmp_path):
    dest = server.build(tmp_path / "srv")

    def digest():
        h = hashlib.sha256()
        for p in sorted(dest.rglob("*")):
            h.update(str(p.relative_to(dest)).encode())
            if p.is_file():
                h.update(p.read_bytes())
        return h.hexdigest()

    before = digest()
    host = FixtureHost(dest)
    scan(host)
    assert digest() == before
    for cmd in host.executed:
        assert tuple(cmd[:2]) in READ_ONLY_COMMANDS, cmd


def test_empty_machine(tmp_path):
    res = scan(FixtureHost(tmp_path))
    assert res.services == []
    assert res.warnings  # every missing source is reported


def test_cli_json(server_dir):
    out = CliRunner().invoke(app, ["scan", "--json", "--fixture", str(server_dir)])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert {s["id"] for s in data["services"]} >= {"app.example.com", "blog.example.com"}


def test_cli_table(server_dir):
    out = CliRunner().invoke(app, ["scan", "--fixture", str(server_dir)], env={"COLUMNS": "250"})
    assert out.exit_code == 0, out.output
    assert "app.example.com" in out.output and "no domain" in out.output
