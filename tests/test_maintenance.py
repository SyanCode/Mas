"""Maintenance mode: the page, the nginx files, the allowed addresses, the end time."""

from __future__ import annotations

import io
import time
import zipfile

import pytest
import server

from mas import templates
from mas.config import Config
from mas.db import Database
from mas.deploy import DeployError, Deployer, vhost_paths
from mas.host import FixtureHost
from mas.maintenance import MAPS_PATH, SNIPPETS_DIR, Maintenance, MaintenanceError, with_base


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands = {"nginx -t": {"output": ""}, "systemctl reload nginx": {"output": ""}, **host._commands}
    db = Database(tmp_path / "mas.db")
    config = Config()
    deployer = Deployer(host, config, db, lambda: None)
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="node", port=3100, domain="shop.example.com")
    db.update_app("shop", mode="managed")
    # a vhost written by an older Mas: no include yet
    old = templates.vhost("shop", "shop.example.com", templates.proxy_body(3100, config.pages_dir), True)
    host.write_text(vhost_paths("shop")[0], old.replace("    include /etc/nginx/mas-maintenance/shop[.]conf;\n", ""))
    return host, db, Maintenance(host, config, db, deployer)


def test_on_and_off(env):
    host, db, m = env
    state = m.enable("shop", "default", ["203.0.113.7", "10.0.0.0/8"], None, "admin")
    assert state["allow"] == ["203.0.113.7/32", "10.0.0.0/8"]
    maps = host.read_text(MAPS_PATH)
    assert "geo $mas_maint_ip_shop" in maps and "203.0.113.7/32 0;" in maps and "acme-challenge" in maps
    snippet = host.read_text(f"{SNIPPETS_DIR}/shop.conf")
    assert "return 503;" in snippet and "/srv/mas/pages/maintenance/default/" in snippet
    assert "include /etc/nginx/mas-maintenance/shop[.]conf;" in host.read_text(vhost_paths("shop")[0])
    page = host.read_text("/srv/mas/pages/maintenance/default/index.html")
    assert '<base href="/__mas_maintenance/">' in page and "Site en maintenance" in page
    assert ["systemctl", "reload", "nginx"] in host.executed
    m.disable("shop")
    assert not host.exists(f"{SNIPPETS_DIR}/shop.conf") and not host.exists(MAPS_PATH)
    assert m.state("shop") is None


def test_refused_by_nginx_restores_everything(env):
    host, db, m = env
    host._commands = {**host._commands, "nginx -t": {"returncode": 1, "stderr": "boom"}}
    before = host.read_text(vhost_paths("shop")[0])
    with pytest.raises(DeployError):
        m.enable("shop", "default", [], None, "admin")
    assert m.state("shop") is None and not host.exists(MAPS_PATH)
    assert host.read_text(vhost_paths("shop")[0]) == before


def test_import_a_zip_and_a_custom_page(env):
    host, db, m = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("site/index.html", "<html><head><title>x</title></head><body><img src='logo.png'></body></html>")
        z.writestr("site/logo.png", b"\x89PNG")
        z.writestr("site/../../etc/passwd", "no")
        z.writestr("site/.env", "no")
    page = m.import_page("Notre page", buf.getvalue())
    assert page == "notre-page"
    root = "/srv/mas/pages/maintenance/notre-page"
    assert host.read_bytes(f"{root}/logo.png") == b"\x89PNG"
    assert '<head><base href="/__mas_maintenance/">' in host.read_text(f"{root}/index.html")
    assert not host.exists(f"{root}/.env") and not host.exists("/srv/mas/etc/passwd")
    with pytest.raises(MaintenanceError):
        m.import_page("x", b"just text")
    custom = m.save_simple(None, "Travaux", "On revient vite", "Mise à jour <b>", "vers 14 h", "#e11d48")
    text = host.read_text(f"/srv/mas/pages/maintenance/{custom}/index.html")
    assert "Mise à jour &lt;b&gt;" in text and "#e11d48" in text
    m.enable("shop", page, [], None, "admin")
    with pytest.raises(MaintenanceError):
        m.delete_page(page)  # in use


def test_ends_by_itself(env):
    host, db, m = env
    m.enable("shop", "default", [], "2099-01-01T10:00", "admin")
    assert m.tick(now=time.time()) == []
    assert m.tick(now=4102444800 + 3600 * 24) == ["shop"]
    assert m.state("shop") is None and not host.exists(f"{SNIPPETS_DIR}/shop.conf")


def test_base_is_added_once():
    assert with_base("<html><head></head></html>").count("<base") == 1
    assert with_base('<head><base href="/x/"></head>').count("<base") == 1
