"""nginx behind Caddy or Apache (mas.front)."""

from __future__ import annotations

import pytest
from test_deploy import SHA1, deploy_once, env  # noqa: F401 - the deploy fixture

from mas import templates
from mas.front import (CADDY_IMPORT, CADDYFILE, REALIP_PATH, Front, FrontError, apache_site, behind,
                       blocking_sites, switch)

DASHBOARD = "server {\n    listen 443 ssl;\n    server_name mas.example.com;\n}\n"


def test_an_nginx_site_behind_the_front_server():
    site = templates.vhost("web", "web.example.com", templates.proxy_body(7100, "/srv/mas/pages"), False)
    out = behind(site, 8088)
    assert "listen 127.0.0.1:8088;" in out and "listen 80" not in out and "[::]:80" not in out
    assert "X-Forwarded-Proto $mas_forwarded_proto" in out and "$scheme" not in out.split("X-Forwarded-Proto", 1)[1][:20]


def test_apache_sites_before_and_after_the_certificate():
    plain = apache_site(["shop.example.com", "www.shop.example.com"], 8088, None)
    assert "<VirtualHost *:80>" in plain and "ProxyPass / http://127.0.0.1:8088/ upgrade=websocket" in plain
    assert "ServerAlias www.shop.example.com" in plain and ":443" not in plain
    tls = apache_site(["shop.example.com"], 8088, "/etc/letsencrypt/live/shop.example.com")
    assert "Redirect permanent / https://shop.example.com/" in tls and "SSLCertificateFile /etc/letsencrypt/live/shop.example.com/fullchain.pem" in tls
    assert 'X-Forwarded-Proto "https"' in tls


def test_caddy_files_are_checked_and_put_back_when_refused(env):  # noqa: F811
    host, db = env["host"], env["db"]
    front = Front(host, db)
    with pytest.raises(FrontError, match="Caddy is not installed"):
        front.configure("caddy")
    host.write_text(CADDYFILE, "{\n    email a@example.com\n}\n\nother.example.org {\n    respond \"hi\"\n}\n")
    front.configure("caddy", 8088)
    assert "set_real_ip_from 127.0.0.1;" in host.read_text(REALIP_PATH)
    host._commands = {"caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile": {"output": "Valid"}, **host._commands}
    path = front.site("web", ["web.example.com", "www.web.example.com"])
    assert host.read_text(path) == ("# Written by Mas: web.example.com → its nginx. Changes are overwritten.\n"
                                    "web.example.com, www.web.example.com {\n    reverse_proxy 127.0.0.1:8088\n}\n")
    assert CADDY_IMPORT in host.read_text(CADDYFILE) and "other.example.org" in host.read_text(CADDYFILE)
    assert ["systemctl", "reload", "caddy"] in host.executed
    # Caddy refuses the next version: the previous one stays, nothing reloads
    host._commands["caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"] = {"returncode": 1, "output": "bad"}
    with pytest.raises(FrontError, match="refused"):
        front.site("web", ["web.example.com", "new.example.com"])
    assert "new.example.com" not in host.read_text(path)
    # a site of Caddy that is not ours is a conflict
    assert front.conflict("other.example.org") == CADDYFILE and front.conflict("web.example.com") is None
    front.configure("off")
    assert front.get() is None and not host.exists(REALIP_PATH)


def test_an_app_deploys_behind_caddy(env):  # noqa: F811
    host, db, deployer = env["host"], env["db"], env["deployer"]
    host.write_text(CADDYFILE, "{\n}\n")
    deployer.front = Front(host, db)
    deployer.front.configure("caddy", 8088)
    deployer.health_check = lambda url, h, t: (True, "HTTP 200")
    rid, ok = deploy_once(env, SHA1)
    log = deployer.log_path("web", rid).read_text()
    assert ok, log
    vhost = host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    assert "listen 127.0.0.1:8088;" in vhost and "listen 443" not in vhost and "ssl_certificate" not in vhost
    assert "web.example.com" in host.read_text("/etc/caddy/mas/web.caddy")
    assert not any(a[:2] == ["certbot", "certonly"] or "certbot" in a for a in host.executed)  # Caddy does HTTPS
    deployer.remove_app("web")
    assert not host.exists("/etc/caddy/mas/web.caddy")


def test_switching_refuses_foreign_sites_then_moves_every_site(env):  # noqa: F811
    host, db, deployer = env["host"], env["db"], env["deployer"]
    host.write_text(CADDYFILE, "{\n}\n")
    front = Front(host, db)
    deployer.front = front
    if blocking_sites(host):  # the fake server serves sites of its own on 80/443
        with pytest.raises(FrontError, match="not apps of Mas"):
            switch(front, deployer, None, "caddy", 8088, 7070, log=lambda *_: None)
        assert front.get() is None  # nothing changed
        for f in host.glob("/etc/nginx/sites-enabled/*") + host.glob("/etc/nginx/conf.d/*.conf"):
            host.remove(f)
    host.write_text("/etc/nginx/sites-enabled/mas.conf", DASHBOARD)
    deployer.health_check = lambda url, h, t: (True, "HTTP 200")
    deploy_once(env, SHA1)
    done = switch(front, deployer, None, "caddy", 8088, 7070, log=lambda *_: None)
    assert any("dashboard mas.example.com" in d for d in done) and any(d.startswith("web:") for d in done)
    assert "reverse_proxy 127.0.0.1:7070" in host.read_text("/etc/caddy/mas/dashboard.caddy")  # straight to Mas
    assert not host.exists("/etc/nginx/sites-enabled/mas.conf") and host.exists("/etc/nginx/sites-enabled/mas.conf.mas-front-off")
    assert "listen 127.0.0.1:8088;" in host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    # and back: nginx serves the dashboard again, the files of Mas leave Caddy
    switch(front, deployer, None, None, 8088, 7070, log=lambda *_: None)
    assert host.exists("/etc/nginx/sites-enabled/mas.conf") and not host.exists("/etc/caddy/mas/web.caddy")
    assert "listen 80;" in host.read_text("/etc/nginx/sites-available/mas-app-web.conf")


def test_health_follows_the_front_mode(tmp_path):
    import server

    from mas.collectors.ports import ListeningSocket
    from mas.health import check_nginx
    from mas.host import FixtureHost
    host = FixtureHost(server.build(tmp_path / "srv"))
    caddy = [ListeningSocket(443, "0.0.0.0", "caddy", [10])]
    front = {"server": "caddy", "port": 8088}
    titles = [c.title for c in check_nginx(host, [], caddy, front)]
    assert "nginx does not listen on 127.0.0.1:8088" in titles and not any("web ports" in t for t in titles)
    ok = caddy + [ListeningSocket(8088, "127.0.0.1", "nginx", [11])]
    assert not [c for c in check_nginx(host, [], ok, front) if "8088" in c.title]
