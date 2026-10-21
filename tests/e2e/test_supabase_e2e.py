"""Supabase pieces against a real Postgres: migrations at deploy, dev database import, CMS tables.

The database container is named ``supabase-db`` like the one of the real stack, with the roles
Supabase creates (anon, authenticated, service_role); the rest of the stack is not needed here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from e2e_helpers import sh

from mas.appdb import AppDatabases, AppDbError
from mas.cms import parse_schema
from mas.cms_supabase import table_sql
from mas.deploy import DeployError, ReleaseLog
from mas.envcrypt import encrypt_env, load_fernet
from mas.supabase import DB_CONTAINER, SupabaseManager

API_URL = "http://127.0.0.1:7101"


def psql(sql: str) -> str:
    return sh("docker", "exec", DB_CONTAINER, "psql", "-U", "postgres", "-d", "postgres", "-qAt", "-c", sql).strip()


def wait_ready(container: str) -> None:
    for _ in range(60):
        if sh("docker", "exec", container, "pg_isready", "-U", "postgres", check=False).find("accepting") >= 0:
            time.sleep(1)  # the entrypoint restarts postgres once after initdb
            if sh("docker", "exec", container, "pg_isready", "-U", "postgres", check=False).find("accepting") >= 0:
                return
        time.sleep(1)
    raise AssertionError(f"{container} did not start")


@pytest.fixture
def database():
    if DB_CONTAINER in sh("docker", "ps", "-a", "--format", "{{.Names}}").split():
        pytest.fail(f"a container named {DB_CONTAINER} already exists: run the end-to-end tests on a throwaway machine")
    sh("docker", "run", "-d", "--name", DB_CONTAINER, "-e", "POSTGRES_PASSWORD=e2e", "postgres:15-alpine")
    try:
        wait_ready(DB_CONTAINER)
        psql("create role anon nologin; create role authenticated nologin; create role service_role nologin;")
        yield
    finally:
        sh("docker", "rm", "-f", DB_CONTAINER, check=False)


@pytest.fixture
def stack(env, database, tmp_path):
    """Mas's Supabase record (running) and an app connected to it."""
    fernet = load_fernet(env.host, env.config.secret_key_path)
    env.db.save_supabase("supabase", status="running", dir=str(tmp_path / "supabase"), domain=None,
                         ports=json.dumps({"kong_http": 7101, "postgres": 5432}),
                         secrets=encrypt_env(fernet, {"ANON_KEY": "anon", "SERVICE_ROLE_KEY": "service",
                                                      "POSTGRES_PASSWORD": "e2e", "DASHBOARD_USERNAME": "u",
                                                      "DASHBOARD_PASSWORD": "p"}))
    supabase = SupabaseManager(env.host, env.config, env.db, env.deployer)
    appdb = AppDatabases(env.host, env.config, env.db, supabase, env.deployer, lambda: None)
    env.db.create_app("web", name="web", repo="acme/web", branch="main", type="static", mode="managed",
                      env=encrypt_env(fernet, {"SUPABASE_URL": API_URL, "SUPABASE_SERVICE_ROLE_KEY": "service"}))
    return supabase, appdb


def release(tmp_path: Path, files: dict[str, str]) -> str:
    rel = tmp_path / "release"
    for name, text in files.items():
        (rel / name).parent.mkdir(parents=True, exist_ok=True)
        (rel / name).write_text(text)
    return str(rel)


def test_migrations_at_deploy(env, stack, tmp_path):
    supabase, appdb = stack
    app = env.db.get_app("web")
    assert appdb.connection(app) == "local"
    env.db.set_setting("supabase_seed:web", "1")
    rel = release(tmp_path, {
        "supabase/migrations/20240101000000_init.sql":
            "create table notes (id serial primary key, body text not null);",
        "supabase/migrations/20240102000000_tags.sql": "alter table notes add column tag text;",
        "supabase/seed.sql": "insert into notes (body, tag) values ('hello', 'demo');",
    })
    appdb.migrate_release(app, rel, ReleaseLog(tmp_path / "1.log"))
    assert psql("select body || '/' || tag from notes") == "hello/demo"
    assert psql("select string_agg(version, ',' order by version) from supabase_migrations.schema_migrations") == \
        "20240101000000,20240102000000"

    # next deploy: nothing applied twice, the seed is not loaded again
    appdb.migrate_release(app, rel, ReleaseLog(tmp_path / "2.log"))
    assert psql("select count(*) from notes") == "1"
    assert appdb.applied() == {"20240101000000", "20240102000000"}

    # a failing migration stops the deploy and leaves nothing half done
    Path(rel, "supabase/migrations/20240103000000_bad.sql").write_text(
        "create table half (id int);\nselect * from does_not_exist;\n")
    with pytest.raises(DeployError, match="does_not_exist"):
        appdb.migrate_release(app, rel, ReleaseLog(tmp_path / "3.log"))
    assert psql("select to_regclass('public.half') is null") == "t"
    assert "20240103000000" not in appdb.applied()


@pytest.fixture(params=["postgres:15-alpine", "postgres:16-alpine"])
def dev_database(request):
    """A dev database elsewhere (supabase.com in real life), as recent as the server or newer."""
    name = f"mas-e2e-source-{request.param.split(':')[1].split('-')[0]}"
    sh("docker", "rm", "-f", name, check=False)
    sh("docker", "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=src", request.param)
    try:
        wait_ready(name)
        sh("docker", "exec", name, "psql", "-U", "postgres", "-c",
           "create table posts (id serial primary key, title text not null); "
           "insert into posts (title) values ('first'), ('second'); "
           "create function post_count() returns bigint language sql as 'select count(*) from posts';")
        ip = sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name).strip()
        yield f"postgresql://postgres:src@{ip}:5432/postgres"
    finally:
        sh("docker", "rm", "-f", name, check=False)


def test_import_a_dev_database(env, stack, dev_database):
    supabase, appdb = stack
    env.db.create_app("shop", name="shop", repo="acme/shop", branch="main", type="static", mode="managed")
    result = appdb.import_dev("shop", dev_database, True, False, "e2e")
    assert result["tables"] == ["posts"] and result["connected"]["variables"]  # copied, then linked
    assert psql("select string_agg(title, ',' order by id) from posts") == "first,second"
    assert psql("select post_count()") == "2"  # functions come along

    with pytest.raises(AppDbError, match="already exist"):
        appdb.import_dev("shop", dev_database, True, False, "e2e")
    appdb.import_dev("shop", dev_database, False, True, "e2e")  # replace, structure only
    assert psql("select count(*) from posts") == "0"
    with pytest.raises(AppDbError, match="cannot read the dev database"):
        appdb.import_dev("shop", dev_database.replace(":src@", ":wrong@"), True, True, "e2e")


def test_cms_table_sql(env, stack):
    supabase, _ = stack
    collection = parse_schema(json.dumps({"collections": [{
        "id": "articles", "storage": "supabase",
        "fields": [{"key": "title"}, {"key": "date", "type": "date"},
                   {"key": "views", "type": "number", "integer": True},
                   {"key": "tags", "type": "list", "fields": [{"key": "name"}]}]}]}))["collections"][0]
    supabase.run_sql(table_sql(collection))
    supabase.run_sql(table_sql(collection))  # again, after "adding fields": no error
    columns = psql("select string_agg(column_name || ':' || data_type, ',' order by ordinal_position) "
                   "from information_schema.columns where table_name = 'articles'")
    assert columns == ("slug:text,title:text,date:date,views:bigint,tags:jsonb,"
                       "created_at:timestamp with time zone,updated_at:timestamp with time zone")
    assert psql("select relrowsecurity from pg_class where relname = 'articles'") == "t"
    assert psql("select roles::text from pg_policies where tablename = 'articles'") == "{anon,authenticated}"
