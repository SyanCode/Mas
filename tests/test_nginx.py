from __future__ import annotations

import pytest
import server

from mas.collectors import ScanContext, nginx
from mas.collectors.nginx import NginxParseError, merge_blocks, parse, parse_target, server_from_directive


def _sites(text: str, upstreams=None):
    blocks = [server_from_directive(d) for d in nginx._walk_servers(parse(text))]
    return merge_blocks(blocks, upstreams or {})


# -- parser ----------------------------------------------------------------


def test_parse_handles_comments_quotes_and_nesting():
    ds = parse("""
        # comment
        server {
            listen 80; # trailing comment
            add_header X-Test "a;b {c}";
            location / { if ($x) { return 404; } }
        }
    """)
    (srv,) = ds
    assert srv.name == "server"
    names = [d.name for d in srv.block]
    assert names == ["listen", "add_header", "location"]
    assert srv.block[1].args == ["X-Test", "a;b {c}"]
    assert srv.block[2].block[0].name == "if"


def test_parse_errors_are_reported():
    with pytest.raises(NginxParseError):
        parse("server { listen 80;")
    with pytest.raises(NginxParseError):
        parse("listen 80; }")


def test_broken_file_does_not_break_collection(server_dir, tmp_path):
    dest = server.build(tmp_path / "srv")
    (dest / "root/etc/nginx/sites-enabled/broken").write_text("server { listen 80;\n")
    from mas.host import FixtureHost

    ctx = ScanContext(FixtureHost(dest))
    sites = nginx.collect(ctx)
    assert any("broken" in w for w in ctx.warnings)
    assert "app.example.com" in {s.primary_name for s in sites}


def test_missing_nginx_dir(tmp_path):
    from mas.host import Host

    ctx = ScanContext(Host(tmp_path))
    assert nginx.collect(ctx) == []
    assert ctx.warnings


# -- targets / listen ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,kind,host,port,socket",
    [
        ("http://127.0.0.1:3000", "tcp", "127.0.0.1", 3000, None),
        ("http://localhost:8000/", "tcp", "localhost", 8000, None),
        ("http://localhost", "tcp", "localhost", 80, None),
        ("https://backend.internal", "tcp", "backend.internal", 443, None),
        ("http://[::1]:5000/api", "tcp", "::1", 5000, None),
        ("http://unix:/run/app.sock:/", "unix", None, None, "/run/app.sock"),
        ("unix:/run/php/php8.2-fpm.sock", "unix", None, None, "/run/php/php8.2-fpm.sock"),
        ("http://api_backend", "tcp", "127.0.0.1", 8000, None),
    ],
)
def test_parse_target(raw, kind, host, port, socket):
    t = parse_target(raw, {"api_backend": ["127.0.0.1:8000"]})
    assert (t.kind, t.host, t.port, t.socket) == (kind, host, port, socket)


def test_dynamic_target():
    assert parse_target("http://$backend", {}).kind == "dynamic"
    assert not parse_target("http://10.0.0.5:80", {}).is_local


def test_listen_variants():
    (site,) = _sites("""
        server {
            listen 127.0.0.1:8080;
            listen [::]:443 ssl http2;
            server_name x.test;
            root /srv/x;
        }
    """)
    assert site.ports == [443, 8080]
    assert site.ssl


# -- the fixture server ----------------------------------------------------


@pytest.fixture
def sites(ctx):
    return {s.primary_name: s for s in nginx.collect(ctx)}


def test_proxy_vhost_with_upstream_and_include(sites):
    app = sites["app.example.com"]
    routes = {r.location: r.target for r in app.proxy}
    assert routes["/"].port == 3000
    assert routes["/api/"].upstream == "api_backend"
    assert routes["/api/"].port == 8000


def test_certbot_redirect_is_merged_into_https_block(sites):
    app = sites["app.example.com"]
    assert app.ssl and app.https_redirect
    assert app.ports == [80, 443]
    assert len(app.files) == 1
    assert app.ssl_expires == server.APP_CERT_EXPIRES
    assert list(sites).count("app.example.com") == 1


def test_php_vhost(sites):
    blog = sites["blog.example.com"]
    assert blog.root == "/var/www/blog/public"
    assert blog.fastcgi[0].target.socket == "/run/php/php8.2-fpm.sock"
    assert blog.https_redirect


def test_www_redirect_becomes_alias(sites):
    assert sites["blog.example.com"].names == ["blog.example.com", "www.blog.example.com"]
    assert "www.blog.example.com" not in sites


def test_static_vhost_with_several_server_names(sites):
    docs = sites["docs.example.com"]
    assert docs.names == ["docs.example.com", "doc.example.com"]
    assert docs.root == "/var/www/docs"
    assert not docs.proxy and not docs.fastcgi


def test_default_server_has_no_domain(sites):
    default = sites[None]
    assert default.default_server
    assert default.root == "/var/www/html"


def test_upstream_in_same_file(sites):
    assert sites["grafana.example.com"].proxy[0].target.port == 3001


def test_redirect_without_https_block_is_kept():
    sites = _sites("server { listen 80; server_name old.test; return 301 https://new.test$request_uri; }")
    assert sites[0].redirect_to == "https://new.test$request_uri"


def test_separate_http_and_https_content_blocks_merge():
    sites = _sites("""
        server { listen 80; server_name a.test www.a.test; location / { proxy_pass http://127.0.0.1:9000; } }
        server { listen 443 ssl; server_name a.test; location / { proxy_pass http://127.0.0.1:9000; } }
    """)
    assert len(sites) == 1
    assert sites[0].names == ["a.test", "www.a.test"]
    assert len(sites[0].proxy) == 1
