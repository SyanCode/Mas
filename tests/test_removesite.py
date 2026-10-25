"""Removing the nginx site of a service that is gone."""

from __future__ import annotations

import pytest
import server

from mas.host import FixtureHost
from mas.models import Service, Status
from mas.removesite import RemoveError, certificates, check, remove

SITE = """server {
    server_name map.example.com;
    ssl_certificate /etc/letsencrypt/live/map.example.com/fullchain.pem;
    location / { proxy_pass http://127.0.0.1:8100; }
}
"""


def svc(files, status=Status.STOPPED, domains=("map.example.com",)):
    return Service(id="map.example.com", name="map", domains=list(domains), nginx_files=files, status=status)


@pytest.fixture
def host(tmp_path):
    h = FixtureHost(server.build(tmp_path / "srv"))
    h.write_text("/etc/nginx/sites-available/map.example.com.conf", SITE)
    h.symlink_atomic("/etc/nginx/sites-available/map.example.com.conf", "/etc/nginx/sites-enabled/map.example.com.conf")
    h._commands = {**h._commands, "nginx -t": {"output": ""}, "systemctl reload nginx": {"output": ""},
                   "certbot delete *": {"output": ""}}
    return h


def test_disables_the_link_and_deletes_the_certificate(host):
    path = "/etc/nginx/sites-enabled/map.example.com.conf"
    assert certificates(host, [path]) == ["map.example.com"]
    done = remove(host, svc([path]), delete_certificate=True)
    assert not host.exists(path) and host.exists("/etc/nginx/sites-available/map.example.com.conf")
    assert ["certbot", "delete", "--non-interactive", "--cert-name", "map.example.com"] in host.executed
    assert "nginx reloaded" in done


def test_refuses_what_is_not_only_this_site(host):
    shared = "/etc/nginx/conf.d/shared.conf"
    host.write_text(shared, SITE.replace("map.example.com;", "map.example.com other.example.com;"))
    with pytest.raises(RemoveError, match="also serves other.example.com"):
        check(host, svc([shared]))
    with pytest.raises(RemoveError, match="stop it first"):
        check(host, svc(["/etc/nginx/sites-enabled/map.example.com.conf"], status=Status.RUNNING))
    with pytest.raises(RemoveError, match="belongs to Mas"):
        check(host, svc(["/etc/nginx/sites-enabled/mas-app-shop.conf"]))


def test_everything_back_when_nginx_refuses(host):
    plain = "/etc/nginx/conf.d/map.conf"
    host.write_text(plain, SITE)
    host._commands = {**host._commands, "nginx -t": {"returncode": 1, "stderr": "boom"}}
    with pytest.raises(RemoveError, match="nothing changed"):
        remove(host, svc([plain, "/etc/nginx/sites-enabled/map.example.com.conf"]))
    assert host.read_text(plain) == SITE and host.exists("/etc/nginx/sites-enabled/map.example.com.conf")
