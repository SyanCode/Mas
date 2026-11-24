"""Sites of Apache and Caddy found by the scan, like the nginx ones."""

from __future__ import annotations

import server

from mas.actions import available_actions, run_action
from mas.collectors import ScanContext
from mas.collectors.webservers import _caddy_blocks, collect_apache, collect_caddy, parse_caddy
from mas.config import Config
from mas.correlator import scan
from mas.host import FixtureHost
from mas.models import Manager

DEBIAN_SITE = """
<VirtualHost *:80>
    ServerName shop.example.com
    ServerAlias www.shop.example.com
    Redirect permanent / https://shop.example.com/
</VirtualHost>
<VirtualHost *:443>
    ServerName shop.example.com
    ServerAlias www.shop.example.com
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/shop.example.com/fullchain.pem
    ProxyPass / http://127.0.0.1:3000/
    ProxyPassReverse / http://127.0.0.1:3000/
    CustomLog ${APACHE_LOG_DIR}/shop.access.log combined
</VirtualHost>
"""

RHEL_BLOG = """
# a PHP site through PHP-FPM
<VirtualHost *:80>
    ServerName blog.example.org
    DocumentRoot "/var/www/blog"
    <FilesMatch \\.php$>
        SetHandler "proxy:unix:/run/php-fpm/www.sock|fcgi://localhost"
    </FilesMatch>
</VirtualHost>
"""

CADDYFILE = """
{
    email admin@example.com
}

(common) {
    encode gzip
}

app.example.net, www.app.example.net {
    import common
    reverse_proxy localhost:8080
    log {
        output file /var/log/caddy/app.log
    }
}

static.example.net {
    root * /srv/static
    file_server
}

php.example.net {
    root * /var/www/php
    php_fastcgi unix//run/php/php8.2-fpm.sock
}

http://plain.example.net:8081 {
    reverse_proxy /api/* 127.0.0.1:9001
}

import sites/*.caddy
"""


def test_apache_on_debian_and_on_rhel(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host.write_text("/etc/apache2/apache2.conf", "ServerRoot /etc/apache2\nIncludeOptional sites-enabled/*.conf\n")
    host.write_text("/etc/apache2/sites-enabled/shop.conf", DEBIAN_SITE)
    sites = collect_apache(ScanContext(host))
    [shop] = sites
    assert shop.server == "apache" and shop.names == ["shop.example.com", "www.shop.example.com"]
    assert shop.ports == [80, 443] and shop.ssl and shop.https_redirect
    assert shop.proxy[0].target.port == 3000 and shop.proxy[0].target.is_local
    assert shop.log_paths == ["/var/log/apache2/shop.access.log"] and shop.redirect_to is None

    host2 = FixtureHost(server.build(tmp_path / "srv2"))
    host2.write_text("/etc/httpd/conf/httpd.conf", 'ServerRoot "/etc/httpd"\nIncludeOptional conf.d/*.conf\n')
    host2.write_text("/etc/httpd/conf.d/blog.conf", RHEL_BLOG)
    [blog] = collect_apache(ScanContext(host2))
    assert blog.names == ["blog.example.org"] and blog.root == "/var/www/blog"
    assert blog.fastcgi[0].target.kind == "unix" and blog.fastcgi[0].target.socket == "/run/php-fpm/www.sock"


def test_caddy_sites_snippets_and_imports(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host.write_text("/etc/caddy/Caddyfile", CADDYFILE)
    host.write_text("/etc/caddy/sites/extra.caddy", "extra.example.net {\n    reverse_proxy 127.0.0.1:7000\n}\n")
    assert [h for h, _ in _caddy_blocks(CADDYFILE)][:2] == ["", "(common)"]
    sites = {s.primary_name: s for s in collect_caddy(ScanContext(host))}
    app = sites["app.example.net"]
    assert app.names == ["app.example.net", "www.app.example.net"] and app.ssl and app.ports == [443]
    assert app.proxy[0].target.port == 8080 and app.log_paths == ["/var/log/caddy/app.log"]
    assert sites["static.example.net"].root == "/srv/static" and not sites["static.example.net"].proxy
    assert sites["php.example.net"].fastcgi[0].target.socket == "/run/php/php8.2-fpm.sock"
    plain = sites["plain.example.net"]
    assert not plain.ssl and plain.ports == [8081] and plain.proxy[0].location == "/api/"
    assert sites["extra.example.net"].proxy[0].target.port == 7000  # from the import
    assert len(parse_caddy([("x", "{\n  admin off\n}\n")])) == 0  # global options only


def test_the_scan_shows_them_and_reloads_them_safely(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host.write_text("/etc/caddy/Caddyfile", "static.example.net {\n    root * /srv/static\n    file_server\n}\n")
    result = scan(host, Config())
    svc = next(s for s in result.services if s.id == "static.example.net")
    assert svc.manager == Manager.CADDY and svc.unit == "caddy.service" and svc.domains == ["static.example.net"]
    assert available_actions(svc) == ["reload"]
    host._commands = {"caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile": {"returncode": 1, "output": "bad"},
                      **host._commands}
    out = run_action(host, svc, "reload")
    assert not out.ok and "NOT reloaded" in out.output and ["systemctl", "reload", "caddy.service"] not in host.executed
    host._commands["caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"] = {"output": "Valid configuration"}
    host._commands["systemctl reload caddy.service"] = {"output": ""}
    assert run_action(host, svc, "reload").ok and ["systemctl", "reload", "caddy.service"] in host.executed


def test_health_says_when_apache_or_caddy_holds_the_web_ports(tmp_path):
    from mas.collectors.ports import ListeningSocket
    from mas.health import check_nginx
    host = FixtureHost(server.build(tmp_path / "srv"))
    caddy = [ListeningSocket(80, "0.0.0.0", "caddy", [10]), ListeningSocket(443, "0.0.0.0", "caddy", [10])]
    [warn] = [c for c in check_nginx(host, [], caddy) if "web ports" in c.title]
    assert warn.title == "Caddy holds the web ports" and "listens on 443" in warn.detail
    both = caddy + [ListeningSocket(80, "0.0.0.0", "nginx", [11])]
    assert not [c for c in check_nginx(host, [], both) if "web ports" in c.title]
