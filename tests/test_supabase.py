"""Self-hosted Supabase: secrets, configuration, install pipeline, connecting apps."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth, templates
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.host import FixtureHost
import yaml

from mas.supabase import (
    DEFAULT_COMPONENTS,
    SupabaseError,
    SupabaseManager,
    apply_profile,
    bind_loopback,
    estimate_ram,
    generate_secrets,
    parse_mem,
    render_env,
)

COMPOSE = """services:
  kong:
    container_name: supabase-kong
    ports:
      - ${KONG_HTTP_PORT}:8000/tcp
      - "${KONG_HTTPS_PORT}:8443/tcp"
  supavisor:
    ports:
      - ${POSTGRES_PORT}:5432
"""
ENV_EXAMPLE = "############\n# Secrets\nPOSTGRES_PASSWORD=your-super-secret\nJWT_SECRET=x\nANON_KEY=x\nKONG_HTTP_PORT=8000\n"


def test_keys_are_valid_hs256_jwts():
    s = generate_secrets(now=1_700_000_000)
    for key, role in (("ANON_KEY", "anon"), ("SERVICE_ROLE_KEY", "service_role")):
        header, payload, sig = s[key].split(".")
        expected = hmac.new(s["JWT_SECRET"].encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == expected
        assert json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["role"] == role
    assert generate_secrets()["JWT_SECRET"] != s["JWT_SECRET"]


def test_render_env_and_loopback_ports():
    env = render_env(ENV_EXAMPLE, {"POSTGRES_PASSWORD": "pw", "KONG_HTTP_PORT": "7100", "SITE_URL": "https://x"})
    assert "POSTGRES_PASSWORD=pw" in env and "KONG_HTTP_PORT=7100" in env and "# Secrets" in env
    assert env.rstrip().endswith("SITE_URL=https://x")
    compose = bind_loopback(COMPOSE)
    assert "- 127.0.0.1:${KONG_HTTP_PORT}:8000/tcp" in compose
    assert '- "127.0.0.1:${KONG_HTTPS_PORT}:8443/tcp"' in compose
    assert "- 127.0.0.1:${POSTGRES_PORT}:5432" in compose


FULL_COMPOSE = """services:
  studio:
    environment:
      STUDIO_PG_META_URL: http://meta:8080
    depends_on:
      analytics:
        condition: service_healthy
  kong:
    ports:
      - ${KONG_HTTP_PORT}:8000/tcp
    environment:
      KONG_DATABASE: "off"
    depends_on:
      analytics:
        condition: service_healthy
  auth:
    depends_on:
      db:
        condition: service_healthy
      analytics:
        condition: service_healthy
  storage:
    environment:
      ENABLE_IMAGE_TRANSFORMATION: "true"
      IMGPROXY_URL: http://imgproxy:5001
    depends_on:
      - db
      - imgproxy
  imgproxy: {}
  meta: {}
  realtime: {}
  functions: {}
  analytics: {}
  vector: {}
  supavisor:
    ports:
      - ${POSTGRES_PORT}:5432
  db:
    command: ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
    depends_on:
      vector:
        condition: service_healthy
"""


def test_light_profile_drops_heavy_services_and_tunes_memory():
    out = apply_profile(FULL_COMPOSE, DEFAULT_COMPONENTS)
    services = yaml.safe_load(out)["services"]
    assert set(services) == {"studio", "kong", "auth", "storage", "meta", "realtime", "db"}
    assert "depends_on" not in services["studio"] and "depends_on" not in services["db"]
    assert list(services["auth"]["depends_on"]) == ["db"]
    assert services["storage"]["depends_on"] == ["db"]
    assert services["storage"]["environment"]["ENABLE_IMAGE_TRANSFORMATION"] == "false"
    assert "IMGPROXY_URL" not in services["storage"]["environment"]
    assert services["db"]["ports"] == ["127.0.0.1:${POSTGRES_PORT}:5432"]  # no pooler: direct, loopback only
    assert "shared_buffers=64MB" in services["db"]["command"]
    assert services["kong"]["environment"]["KONG_NGINX_WORKER_PROCESSES"] == "1"
    assert services["kong"]["ports"] == ["127.0.0.1:${KONG_HTTP_PORT}:8000/tcp"]
    assert services["kong"]["environment"]["KONG_DATABASE"] == "off"  # quoting survives the round trip
    assert services["studio"]["environment"]["NODE_OPTIONS"] == "--max-old-space-size=256"
    assert estimate_ram(DEFAULT_COMPONENTS) < estimate_ram(["studio", "realtime", "storage", "images", "functions",
                                                            "logs", "pooler"])


def test_core_only_and_full_profiles():
    core = yaml.safe_load(apply_profile(FULL_COMPOSE, []))["services"]
    assert set(core) == {"kong", "auth", "db"}
    full = yaml.safe_load(apply_profile(FULL_COMPOSE, ["studio", "realtime", "storage", "images", "functions",
                                                       "logs", "pooler"]))["services"]
    assert "ports" not in full["db"] and full["supavisor"]["ports"] == ["127.0.0.1:${POSTGRES_PORT}:5432"]
    assert full["storage"]["environment"]["IMGPROXY_URL"] == "http://imgproxy:5001"


def test_parse_docker_memory():
    assert parse_mem("123.4MiB") == 123.4
    assert parse_mem("1.5GiB") == 1536
    assert parse_mem("512KiB") == 0.5
    assert parse_mem("garbage") == 0


def test_only_the_public_api_is_proxied():
    body = templates.supabase_body(7100, "/srv/mas/pages")
    assert "location ~ ^/(rest|auth|realtime|storage|functions|graphql)/v1/" in body
    assert "location / {\n        return 404;" in body  # Studio / pg-meta stay private


@pytest.fixture
def manager(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host.write_text("/proc/meminfo", "MemTotal: 8000000 kB\nMemAvailable: 4000000 kB\n")

    def on_stream(argv, cwd, env):
        if argv[:2] == ["git", "clone"]:
            docker = Path(argv[-1]) / "docker"
            docker.mkdir(parents=True)
            (docker / "docker-compose.yml").write_text(COMPOSE)
            (docker / ".env.example").write_text(ENV_EXAMPLE)

    host.stream_hooks.append(on_stream)
    host._commands = {"docker version *": {"output": "27.3.1\n"}, "docker compose version *": {"output": "2.29.7\n"},
                      "docker ps *": {"output": ""}, "git -C * rev-parse HEAD": {"output": "a" * 40},
                      **host._commands, "*": {"returncode": 0}}
    db = Database(tmp_path / "mas.db")
    config = Config(secret_key_path="/etc/mas/secret.key", dns_wait_seconds=0)
    deployer = Deployer(host, config, db, lambda: None)
    return SupabaseManager(host, config, db, deployer, health=lambda url, key, t: (True, "HTTP 200")), host, db


def test_install_pipeline(manager):
    mgr, host, db = manager
    db.save_supabase("supabase", status="installing", dir=mgr.dir)
    assert mgr.install(), mgr.log_path().read_text()
    inst = mgr.get()
    assert inst["status"] == "running" and inst["ref"] == "a" * 40
    env = host.read_text(f"{mgr.dir}/.env")
    secrets = mgr.secrets(inst)
    assert f"ANON_KEY={secrets['ANON_KEY']}" in env and "POSTGRES_PASSWORD=your-super-secret" not in env
    assert "127.0.0.1:${KONG_HTTP_PORT}" in host.read_text(f"{mgr.dir}/docker-compose.yml")
    ports = mgr.ports(inst)
    assert f"API_GW_HTTP_PORT={ports['kong_http']}" in env  # Envoy listens where Mas waits for the API
    assert secrets["SERVICE_ROLE_KEY"] not in inst["secrets"]  # encrypted at rest
    ports = mgr.ports(inst)
    assert len(set(ports.values())) == 4 and all(7100 <= p <= 7999 for p in ports.values())
    assert any(c[:4] == ["docker", "compose", "-p", "mas-supabase"] and c[-2:] == ["up", "-d"] for c in host.executed)
    env_vars = mgr.connection_env()
    assert env_vars["NEXT_PUBLIC_SUPABASE_URL"] == f"http://127.0.0.1:{ports['kong_http']}"
    assert env_vars["DATABASE_URL"].endswith(f"@127.0.0.1:{ports['postgres']}/postgres")


def test_reconfigure_studio_and_stats(manager):
    mgr, host, db = manager
    db.save_supabase("supabase", status="installing", dir=mgr.dir, components=json.dumps(["studio"]))
    assert mgr.install(), mgr.log_path().read_text()
    assert "supavisor" in host.read_text(f"{mgr.dir}/docker-compose.original.yml")
    assert "supavisor" not in host.read_text(f"{mgr.dir}/docker-compose.yml")
    mgr.reconfigure(["pooler", "bogus"])
    assert mgr.components(mgr.get()) == ["pooler"]
    assert "supavisor" in host.read_text(f"{mgr.dir}/docker-compose.yml")
    assert any(c[-3:] == ["up", "-d", "--remove-orphans"] for c in host.executed)
    with pytest.raises(SupabaseError):
        mgr.studio("start")  # Studio is no longer enabled
    mgr.reconfigure(["studio"])
    mgr.studio("stop")
    assert any(c[-3:] == ["stop", "studio", "meta"] for c in host.executed)
    host._commands = {"docker ps *": {"output": "supabase-db\nsupabase-kong\n"},
                      "docker stats *": {"output": "supabase-db|180.5MiB / 7.6GiB\nsupabase-kong|1.1GiB / 7.6GiB\n"}}
    stats = mgr.stats()
    assert [r["name"] for r in stats["containers"]] == ["supabase-kong", "supabase-db"]
    assert stats["total_mb"] == round(1126.4 + 180.5, 1)


def test_install_refuses_without_compose(manager):
    mgr, host, db = manager
    host._commands["docker compose version *"] = {"returncode": 1}
    db.save_supabase("supabase", status="installing", dir=mgr.dir)
    assert not mgr.install()
    assert "compose" in mgr.get()["error"]


def test_connect_supabase_to_an_app(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    config = Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key")
    app = create_app(host, config, db, background=False)
    queued = []
    app.state.deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: queued.append(app_id) or 3
    client = TestClient(app)
    h = {"X-CSRF-Token": client.post("/api/login", json={"username": "admin",
                                                          "password": "correct horse battery"}).json()["csrf"]}
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7300)
    assert client.post("/api/apps/web/supabase", headers=h).status_code == 409  # not installed

    fernet = load_fernet(host, config.secret_key_path)
    s = generate_secrets()
    keep = ("POSTGRES_PASSWORD", "JWT_SECRET", "ANON_KEY", "SERVICE_ROLE_KEY", "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD")
    db.save_supabase("supabase", status="running", dir="/srv/mas/supabase/supabase", domain="db.example.com",
                     ports=json.dumps({"kong_http": 7100, "kong_https": 7101, "postgres": 7102, "pooler": 7103}),
                     secrets=encrypt_env(fernet, {k: s[k] for k in keep}))
    r = client.post("/api/apps/web/supabase", headers=h)
    assert r.status_code == 200 and queued == ["web"]
    env = decrypt_env(fernet, db.get_app("web")["env"])
    assert env["NEXT_PUBLIC_SUPABASE_URL"] == "https://db.example.com"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == s["SERVICE_ROLE_KEY"]
    status = client.get("/api/supabase").json()
    assert status["instance"]["api_url"] == "https://db.example.com" and "secrets" not in status["instance"]


def test_envoy_gateway_ports_are_bound_to_loopback():
    compose = """services:
  api-gw:
    container_name: supabase-envoy
    ports:
      - ${API_GW_HTTP_PORT:-${KONG_HTTP_PORT:-8000}}:8000/tcp
      - 9000:9000
      - 127.0.0.1:9001:9001
      - 9002
      - target: 9003
        published: 9003
"""
    ports = yaml.safe_load(apply_profile(compose, DEFAULT_COMPONENTS))["services"]["api-gw"]["ports"]
    assert ports[:4] == ["127.0.0.1:${API_GW_HTTP_PORT:-${KONG_HTTP_PORT:-8000}}:8000/tcp", "127.0.0.1:9000:9000",
                         "127.0.0.1:9001:9001", 9002]
    assert ports[4]["host_ip"] == "127.0.0.1"
