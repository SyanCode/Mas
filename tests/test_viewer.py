"""The read-only role: the whole dashboard to look at, nothing to change, no secret."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mas import auth
from tests.test_api import PASSWORD, env, login  # noqa: F401 - the env fixture


def viewer_client(client, db):
    db.create_user("eve", auth.hash_password(PASSWORD), role="viewer")
    other = TestClient(client.app)
    r = other.post("/api/login", json={"username": "eve", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    return other, {"X-CSRF-Token": r.json()["csrf"]}


def test_viewer_reads_everything_but_changes_nothing(env):  # noqa: F811
    client, db, uid, _ = env
    other, h = viewer_client(client, db)
    assert other.get("/api/services").status_code == 200
    assert other.get("/api/history").status_code == 200
    for method, path in (("post", "/api/scan"), ("post", "/api/apps"), ("delete", "/api/apps/x")):
        r = other.request(method.upper(), path, headers=h, json={})
        assert r.status_code == 403 and r.json()["detail"] == "read-only account", (path, r.text)


def test_viewer_never_reaches_accounts_or_secrets(env):  # noqa: F811
    client, db, uid, _ = env
    other, h = viewer_client(client, db)
    for path in ("/api/users", "/api/invites", "/api/tokens", "/api/supabase/credentials", "/api/apps/x/env",
                 "/api/mcp", "/api/host-alerts"):
        assert other.get(path).status_code == 403, path


def test_viewer_cannot_use_the_cms_of_sites_nor_become_admin(env):  # noqa: F811
    client, db, uid, _ = env
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com")
    other, h = viewer_client(client, db)
    assert other.get("/api/cms/sites/shop/entries").status_code in (403, 404)
    # an administrator creates and promotes accounts from the dashboard
    csrf = login(client)
    r = client.post("/api/users", headers={"X-CSRF-Token": csrf}, json={"username": "bob", "role": "viewer"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    assert client.post("/api/users", headers={"X-CSRF-Token": csrf}, json={"username": "rob", "role": "admin"}).status_code == 400
    assert other.post("/api/users", headers=h, json={"username": "x", "role": "viewer"}).status_code == 403


def test_cli_creates_a_viewer_and_refuses_unknown_roles(tmp_path):
    import server
    from typer.testing import CliRunner

    from mas.cli import app
    from mas.db import Database

    fixture = server.build(tmp_path / "srv")
    base = ["--fixture", str(fixture), "--db", str(tmp_path / "mas.db")]
    run = CliRunner()
    ok = run.invoke(app, [*base, "user", "create", "eve", "--role", "viewer", "--no-2fa"],
                    input=f"{PASSWORD}\n{PASSWORD}\n")
    assert ok.exit_code == 0, ok.output
    assert Database(tmp_path / "mas.db").get_user("eve")["role"] == "viewer"
    bad = run.invoke(app, [*base, "user", "create", "mallory", "--role", "editor", "--no-2fa"],
                     input=f"{PASSWORD}\n{PASSWORD}\n")
    assert bad.exit_code != 0 and Database(tmp_path / "mas.db").get_user("mallory") is None
