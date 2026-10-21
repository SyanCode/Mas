"""Apps and the Supabase of this server: detection, connection, migrations at deploy, dev imports."""

from __future__ import annotations

import json

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.appdb import connection_variables, detect, role_of
from mas.config import Config
from mas.db import Database
from mas.deploy import DeployError, ReleaseLog
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
PACKAGE = json.dumps({"dependencies": {"@supabase/supabase-js": "^2.45.0", "react": "^19"},
                      "devDependencies": {"vite": "^6"}})
ENV_EXAMPLE = ("# Supabase\nVITE_SUPABASE_URL=\nVITE_SUPABASE_ANON_KEY=\nVITE_SUPABASE_SERVICE_ROLE_KEY=\n"
               "export SUPABASE_SECRET_KEY=x\nDIRECT_URL=\nOTHER=1\n")


def test_detection_reads_the_repository():
    paths = ["web/package.json", "web/.env.example", "web/supabase/config.toml", "web/supabase/seed.sql",
             "web/supabase/migrations/20240102000000_posts.sql", "web/supabase/migrations/20240101000000_init.sql",
             "web/supabase/migrations/notes.md", "supabase/migrations/1_other_app.sql"]
    found = detect({"web/package.json": PACKAGE, "web/.env.example": ENV_EXAMPLE}, paths, root="web")
    assert found["uses_supabase"] and found["folder"] == "web/supabase" and found["seed"]
    assert [m["file"] for m in found["migrations"]] == ["20240101000000_init.sql", "20240102000000_posts.sql"]
    assert found["migrations"][0] == {"version": "20240101000000", "name": "init", "file": "20240101000000_init.sql"}
    assert found["variables"] == {"VITE_SUPABASE_URL": "url", "VITE_SUPABASE_ANON_KEY": "anon_key",
                                  "VITE_SUPABASE_SERVICE_ROLE_KEY": "service_role_key",
                                  "SUPABASE_SECRET_KEY": "service_role_key", "DIRECT_URL": "database_url"}
    assert found["prefixes"] == ["VITE_"]
    assert not detect({"package.json": '{"dependencies": {"react": "19"}}'}, ["package.json"])["uses_supabase"]
    assert detect({"requirements.txt": "fastapi\nsupabase==2.9\n"}, ["requirements.txt"])["uses_supabase"]
    for name, role in (("PUBLIC_SUPABASE_URL", "url"), ("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "anon_key"),
                       ("SUPABASE_KEY", "anon_key"), ("SUPABASE_SERVICE_ROLE_KEY", "service_role_key"),
                       ("POSTGRES_PRISMA_URL", "database_url"), ("STRIPE_KEY", None)):
        assert role_of(name) == role, name


def test_secrets_never_go_to_the_browser():
    found = detect({"package.json": PACKAGE, ".env.example": ENV_EXAMPLE}, ["package.json", ".env.example"])
    creds = {"url": "https://db.example.com", "anon_key": "anon", "service_role_key": "service",
             "database_url": "postgresql://postgres:pw@127.0.0.1:7102/postgres"}
    env, refused = connection_variables(found, creds)
    assert env["VITE_SUPABASE_URL"] == "https://db.example.com" and env["VITE_SUPABASE_ANON_KEY"] == "anon"
    assert env["NEXT_PUBLIC_SUPABASE_URL"] == "https://db.example.com"  # the usual names are always there
    assert env["SUPABASE_SECRET_KEY"] == "service" and env["DIRECT_URL"].startswith("postgresql://")
    assert refused == ["VITE_SUPABASE_SERVICE_ROLE_KEY"] and "VITE_SUPABASE_SERVICE_ROLE_KEY" not in env


class Repo:
    """GitHub for one repository: {path: text}."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def head(self, installation_id, repo, branch):
        return "c0"

    def tree(self, installation_id, repo, ref):
        return [{"path": p, "size": len(d)} for p, d in self.files.items()]

    def read_file(self, installation_id, repo, path, ref):
        return (self.files[path].encode(), "sha") if path in self.files else (None, None)


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    config = Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key")
    app = create_app(host, config, db, background=False)
    queued: list[str] = []
    app.state.deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: queued.append(app_id) or 9
    repo = Repo({"package.json": PACKAGE, ".env.example": ENV_EXAMPLE, "supabase/seed.sql": "insert;",
                 "supabase/migrations/20240101000000_init.sql": "create table a();",
                 "supabase/migrations/20240102000000_posts.sql": "create table posts();"})
    appdb = app.state.appdb
    appdb.github = lambda: repo
    db.create_app("web", name="web", repo="acme/web", branch="main", installation_id=7, type="static",
                  mode="managed")
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]
    return client, db, host, config, appdb, queued, {"X-CSRF-Token": csrf}


def run_supabase(db, host, config):
    fernet = load_fernet(host, config.secret_key_path)
    db.save_supabase("supabase", status="running", dir="/srv/mas/supabase/supabase", domain="db.example.com",
                     ports=json.dumps({"kong_http": 7100, "postgres": 7102}),
                     secrets=encrypt_env(fernet, {"ANON_KEY": "anon", "SERVICE_ROLE_KEY": "service",
                                                  "POSTGRES_PASSWORD": "pw", "DASHBOARD_USERNAME": "u",
                                                  "DASHBOARD_PASSWORD": "p"}))


def test_connect_reads_the_repository(env):
    client, db, host, config, appdb, queued, h = env
    info = client.get("/api/apps/web/database").json()
    assert info["instance"] is None and info["connected"] is None and info["found"]["uses_supabase"]
    assert [m["applied"] for m in info["migrations"]] == [None, None]
    assert client.post("/api/apps/web/supabase", headers=h).status_code == 409  # nothing running yet

    run_supabase(db, host, config)
    r = client.post("/api/apps/web/supabase", json={"seed": True}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["refused"] == ["VITE_SUPABASE_SERVICE_ROLE_KEY"] and r.json()["migrations"] == 2
    assert queued == ["web"]
    app_env = decrypt_env(load_fernet(host, config.secret_key_path), db.get_app("web")["env"])
    assert app_env["VITE_SUPABASE_URL"] == "https://db.example.com" and app_env["VITE_SUPABASE_ANON_KEY"] == "anon"
    assert app_env["SUPABASE_SECRET_KEY"] == "service" and "VITE_SUPABASE_SERVICE_ROLE_KEY" not in app_env
    host._commands["docker exec supabase-db psql * -qAt -c *schema_migrations*"] = {"output": "20240101000000\n"}
    info = client.get("/api/apps/web/database").json()
    assert info["connected"] == "local" and info["seed_pending"]
    assert [m["applied"] for m in info["migrations"]] == [True, False]
    assert "VITE_SUPABASE_URL" in info["variables"]


def release(host, files: dict[str, str]) -> str:
    rel = "/srv/mas/apps/web/releases/abc"
    for path, text in files.items():
        host.makedirs(f"{rel}/{path.rsplit('/', 1)[0]}")
        host.write_text(f"{rel}/{path}", text)
    return rel


def test_migrations_run_before_the_build(env, tmp_path):
    client, db, host, config, appdb, queued, h = env
    run_supabase(db, host, config)
    appdb.connect("web", "admin", seed=True)
    rel = release(host, {"supabase/migrations/20240101000000_init.sql": "create table a();",
                         "supabase/migrations/20240102000000_posts.sql": "create table posts();",
                         "supabase/migrations/readme.md": "x", "supabase/seed.sql": "insert into a values ();"})
    host._commands["docker exec supabase-db psql * -qAt -c *schema_migrations*"] = {"output": "20240101000000\n"}
    host._commands["docker exec supabase-db psql * -c notify*"] = {"returncode": 0}
    host._commands["docker cp *"] = {"returncode": 0}
    host._commands["docker exec supabase-db psql * -f /tmp/mas-*"] = {"returncode": 0}
    scripts: list[str] = []
    real_run = appdb.supabase.run_sql_file
    appdb.supabase.run_sql_file = lambda sql, single_transaction=True: scripts.append(sql) or real_run(sql)
    lines: list[str] = []
    appdb.migrate_release(db.get_app("web"), rel, ReleaseLog(tmp_path / "r.log", lines.append))
    assert len(scripts) == 2  # the second migration, then the seed
    assert "create table posts();" in scripts[0]
    assert "values ('20240102000000', 'posts')" in scripts[0] and "init" not in scripts[0]
    assert scripts[1] == "insert into a values ();"
    assert any("1 migration(s) applied, 1 already there" in line for line in lines)
    assert db.get_setting("supabase_seed:web") is None  # loaded once
    assert any(a[:2] == ["docker", "cp"] and a[3].startswith("supabase-db:/tmp/mas-") for a in host.executed)

    host._commands["docker exec supabase-db psql * -f /tmp/mas-*"] = {"returncode": 3, "stderr": "ERROR: boom"}
    with pytest.raises(DeployError, match="boom"):
        appdb.migrate_release(db.get_app("web"), rel, ReleaseLog(tmp_path / "r2.log"))
    # an app connected elsewhere (supabase.com) is left alone
    db.update_app("web", env=encrypt_env(load_fernet(host, config.secret_key_path),
                                         {"SUPABASE_URL": "https://x.supabase.co"}))
    appdb.migrate_release(db.get_app("web"), rel, ReleaseLog(tmp_path / "r3.log"))


def test_connected_after_install(env, tmp_path):
    client, db, host, config, appdb, queued, h = env
    started = []
    appdb.supabase.start_install = lambda domain, components=None: started.append((domain, components))
    r = client.post("/api/supabase", json={"domain": None, "components": [], "connect": ["web"], "seed": True},
                    headers=h)
    assert r.status_code == 200, r.text
    assert started == [(None, [])] and client.get("/api/apps/web/database").json()["queued"]
    assert client.post("/api/supabase", json={"connect": ["nope"]}, headers=h).status_code == 400
    run_supabase(db, host, config)
    lines: list[str] = []
    for hook in appdb.supabase.on_installed:
        hook(ReleaseLog(tmp_path / "i.log", lines.append))
    assert queued == ["web"] and any(line.startswith("connected web:") for line in lines)
    assert not client.get("/api/apps/web/database").json()["queued"]
    assert db.get_setting("supabase_seed:web") == "1"


def test_new_app_connected_before_its_first_build(env):
    client, db, host, config, appdb, queued, h = env
    run_supabase(db, host, config)
    found = client.get("/api/github/database", params={"repo": "acme/web", "installation_id": 7, "ref": "main"})
    assert found.json()["uses_supabase"] and len(found.json()["migrations"]) == 2
    body = {"id": "shop", "repo": "acme/web", "branch": "main", "installation_id": 7, "type": "static",
            "supabase": True}
    r = client.post("/api/apps", json=body, headers=h)
    assert r.status_code == 200, r.text
    app_env = decrypt_env(load_fernet(host, config.secret_key_path), db.get_app("shop")["env"])
    assert app_env["VITE_SUPABASE_URL"] == "https://db.example.com"
    assert queued == ["shop"]  # one deploy, already connected


def test_import_a_dev_database(env):
    client, db, host, config, appdb, queued, h = env
    run_supabase(db, host, config)
    url = "postgresql://postgres.abc:s3cret@aws-0-eu.pooler.supabase.com:5432/postgres"
    seen: list[tuple[list[str], dict]] = []
    host.stream_hooks.append(lambda argv, cwd, env: seen.append((argv, env or {})))
    host._commands["docker exec -e SRC supabase-db sh -c *pg_tables*"] = {"output": "posts\nprofiles\n"}
    host._commands["docker exec -e SRC supabase-db sh -c *server_version_num*"] = {"output": "170004\n"}
    host._commands["docker exec supabase-db psql * -qAt -c select tablename*"] = {"output": "posts\n"}
    host._commands["docker exec supabase-db psql * -c drop table*"] = {"returncode": 0}
    host._commands["docker exec supabase-db psql * -c notify*"] = {"returncode": 0}
    host._commands["bash -o pipefail -c *"] = {"returncode": 0}

    bad = client.post("/api/apps/web/database/import", json={"url": "mysql://x@y/z"}, headers=h)
    assert bad.status_code == 400
    r = client.post("/api/apps/web/database/import", json={"url": url}, headers=h)
    assert r.status_code == 400 and "posts" in r.json()["detail"]  # would overwrite a table
    r = client.post("/api/apps/web/database/import", json={"url": url, "replace": True, "with_data": False},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == ["posts", "profiles"] and r.json()["replaced"] == ["posts"]
    assert r.json()["connected"]["variables"]  # linked to the app right away
    assert queued == ["web"]
    script = next(argv for argv, _ in seen if argv[0] == "bash")[-1]
    assert "postgres:17-alpine" in script and "--schema-only" in script and "--schema=public" in script
    assert all(url not in " ".join(argv) for argv, _ in seen)  # only in the environment
    assert all(env.get("SRC") == url for _, env in seen)
    assert any('drop table if exists public."posts" cascade' in " ".join(a) for a in host.executed)
    history = db.history(None, 20)
    assert any(e["action"] == "supabase-import" and e["ok"] and "pooler.supabase.com" in e["detail"]
               and "s3cret" not in e["detail"] for e in history)
