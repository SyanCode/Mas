"""Phase 2.5 (health checks) and phase 3 (GitHub, deployment pipeline)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import server
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from mas import auth, templates
from mas.api import create_app
from mas.config import Config, load_config, save_section
from mas.db import Database
from mas.deploy import DeployError, Deployer, app_paths, used_ports, validate_app, validate_backends
from mas.envcrypt import EnvError, decrypt_env, encrypt_env, load_fernet, parse_env, systemd_env_file
from mas.github import app_jwt, git_auth_env, repo_from_remote, verify_signature
from mas.health import check_certificates, check_dns, check_nginx, load_lineages
from mas.host import FixtureHost

SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40


# -- GitHub helpers ----------------------------------------------------------------


def test_app_jwt_is_a_valid_rs256_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    token = app_jwt(1234, pem, now=1_700_000_000)
    header, payload, signature = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(payload))) == {"iat": 1_699_999_940, "exp": 1_700_000_540,
                                                                    "iss": "1234"}
    key.public_key().verify(base64.urlsafe_b64decode(pad(signature)), f"{header}.{payload}".encode(),
                            padding.PKCS1v15(), hashes.SHA256())


def test_webhook_signature():
    body = b'{"zen":"hi"}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, good)
    assert not verify_signature("s3cret", body + b" ", good)
    assert not verify_signature("other", body, good)
    assert not verify_signature("s3cret", body, None)
    assert not verify_signature(None, body, good)


def test_git_token_never_in_argv():
    env = git_auth_env("ghs_TOKEN")
    assert "ghs_TOKEN" not in json.dumps(env)  # base64 in an env var, not in argv
    assert base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]) == b"x-access-token:ghs_TOKEN"


def test_repo_from_remote():
    assert repo_from_remote("git@github.com:acme/web.git") == "acme/web"
    assert repo_from_remote("https://github.com/acme/api") == "acme/api"
    assert repo_from_remote("https://gitlab.com/acme/api") is None


# -- secrets & config ----------------------------------------------------------------


def test_env_parsing_and_encryption(tmp_path):
    from mas.host import Host

    env = parse_env('# comment\nexport A=1\nB="two words"\nC=\'x"y\'\n\nDATABASE_URL=postgres://u:p@h/db\n')
    assert env == {"A": "1", "B": "two words", "C": 'x"y', "DATABASE_URL": "postgres://u:p@h/db"}
    with pytest.raises(EnvError):
        parse_env("not a pair")
    host = Host(tmp_path)
    fernet = load_fernet(host, "/etc/mas/secret.key")
    token = encrypt_env(fernet, env)
    assert "postgres" not in token
    assert decrypt_env(load_fernet(host, "/etc/mas/secret.key"), token) == env
    assert systemd_env_file({"C": 'x"y', "D": "a\\b"}) == 'C="x\\"y"\nD="a\\\\b"\n'
    with pytest.raises(EnvError):
        systemd_env_file({"X": "multi\nline"})


def test_save_section_keeps_the_rest(tmp_path):
    from mas.host import Host

    host = Host(tmp_path)
    host.write_text("/etc/mas/config.toml",
                    "[server]\nport = 7070\n\n[github]\napp_id = 1\n\n[scan]\nhide_os_daemons = false\n")
    save_section(host, "github", {"app_id": 42, "app_slug": "mas-test", "webhook_secret": 'a"b'})
    cfg = load_config(host)
    assert (cfg.port, cfg.hide_os_daemons) == (7070, False)
    assert (cfg.github_app_id, cfg.github_app_slug, cfg.github_webhook_secret) == (42, "mas-test", 'a"b')
    assert any(p.name.startswith("config.toml.mas-bak-") for p in (tmp_path / "etc/mas").iterdir())


def test_validation():
    validate_app("web", "acme/web", "main", "web.example.com", "nextjs")
    for bad in [("Web", "acme/web", "main", None, "node"), ("web", "acme", "main", None, "node"),
                ("web", "acme/web", "../x", None, "node"), ("web", "acme/web", "main", "not a domain", "node"),
                ("web", "acme/web", "main", None, "unknown")]:
        with pytest.raises(DeployError):
            validate_app(*bad)


def test_templates():
    vhost = templates.vhost("web", "web.example.com", templates.proxy_body(7100, "/srv/mas/pages"), https=True)
    assert "proxy_pass http://127.0.0.1:7100;" in vhost and "/etc/letsencrypt/live/web.example.com/" in vhost
    assert "return 301 https://$host$request_uri;" in vhost
    assert "error_page 502 503 504 =503 /__mas/status.html;" in vhost  # restart gap shows the status page
    placeholder = templates.vhost("web", "web.example.com", templates.placeholder_body("/srv/mas/pages", "deploying"),
                                  https=False)
    assert "return 503;" in placeholder and "alias /srv/mas/pages/deploying.html;" in placeholder
    assert "Deploying" in templates.status_page("deploying")
    assert "ssl_certificate" not in templates.vhost("web", "web.example.com", templates.static_body("/x"), https=False)
    assert templates.systemd_quote('gunicorn -b "127.0.0.1:$PORT" 50%') == \
        '/bin/sh -c "exec gunicorn -b \\"127.0.0.1:$PORT\\" 50%%"'


# -- pipeline ----------------------------------------------------------------------------


class FakeGitHub:
    def __init__(self, shas):
        self.shas = list(shas)

    def token(self, installation_id):
        return "ghs_secret_token"

    def installation_for(self, repo):
        return 99

    def commit(self, installation_id, repo, ref):
        return {"sha": self.shas[0], "message": f"commit {self.shas[0][:3]}", "author": "Alice"}


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config(apps_dir="/srv/mas/apps", keep_releases=2, healthcheck_timeout=1,
                    secret_key_path="/etc/mas/secret.key")
    gh = FakeGitHub([SHA1])
    health = {"ok": True}
    calls: list = []

    def on_stream(argv, cwd, env_vars):
        calls.append((argv, env_vars))
        if argv[:2] == ["git", "clone"]:
            dest = Path(argv[-1])
            dest.mkdir(parents=True)
            (dest / "package.json").write_text(json.dumps({"dependencies": {"next": "15"},
                                                           "scripts": {"build": "next build"}}))
            (dest / "package-lock.json").write_text("{}")

    host.stream_hooks.append(on_stream)
    host._commands = {"git -C * rev-parse HEAD": {"output": SHA1}, **host._commands, "*": {"returncode": 0}}

    def set_sha(sha):
        gh.shas = [sha]
        host._commands["git -C * rev-parse HEAD"] = {"output": sha}

    deployer = Deployer(host, config, db, lambda: gh,
                        health_check=lambda url, h, t: (health["ok"], "HTTP 200" if health["ok"] else "refused"))
    fernet = load_fernet(host, config.secret_key_path)
    db.create_app("web", name="web", repo="acme/web", branch="main", installation_id=99,
                  domain="web.example.com", type="nextjs", port=7100,
                  env=encrypt_env(fernet, {"API_KEY": "s3cr3t"}))
    return {"host": host, "db": db, "config": config, "gh": gh, "deployer": deployer, "health": health,
            "calls": calls, "set_sha": set_sha}


def deploy_once(env, sha):
    env["set_sha"](sha)
    rid = env["db"].create_release("web", "manual", "test")
    return rid, env["deployer"].run(rid)


def test_load_balanced_instances_and_backends(env):
    host, db, deployer = env["host"], env["db"], env["deployer"]
    checked = []
    deployer.health_check = lambda url, h, t: (checked.append(url) or True, "HTTP 200")
    db.update_app("web", extra_ports=json.dumps([7101, 7102]), backends=json.dumps(["10.0.0.12:3000"]))
    rid, ok = deploy_once(env, SHA1)
    assert ok, deployer.log_path("web", rid).read_text()

    for unit, port in (("mas-app-web.service", 7100), ("mas-app-web.2.service", 7101),
                       ("mas-app-web.3.service", 7102)):
        assert f"-p {port}" in host.read_text(f"/etc/systemd/system/{unit}")
    restarts = [a[-1] for a in host.executed if a[:2] == ["systemctl", "restart"]]
    assert restarts[-3:] == ["mas-app-web.service", "mas-app-web.2.service", "mas-app-web.3.service"]
    assert {"http://127.0.0.1:7100/", "http://127.0.0.1:7101/", "http://127.0.0.1:7102/"} <= set(checked)
    vhost = host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    assert vhost.index("upstream mas_app_web {") < vhost.index("server {")
    for server in ("127.0.0.1:7100", "127.0.0.1:7101", "127.0.0.1:7102", "10.0.0.12:3000"):
        assert f"server {server} max_fails=2 fail_timeout=10s;" in vhost
    assert "proxy_pass http://mas_app_web;" in vhost and "proxy_next_upstream error timeout" in vhost
    assert 7101 in used_ports(host, db) and 7102 in used_ports(host, db)

    # scale back down: extra units go away, the vhost proxies straight to the app again
    db.update_app("web", extra_ports="[]", backends="[]")
    rid, ok = deploy_once(env, SHA2)
    assert ok, deployer.log_path("web", rid).read_text()
    assert not host.exists("/etc/systemd/system/mas-app-web.2.service")
    assert not host.exists("/etc/systemd/system/mas-app-web.3.service")
    vhost = host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    assert "upstream" not in vhost and "proxy_pass http://127.0.0.1:7100;" in vhost


def test_backends_are_validated():
    assert validate_backends([" 10.0.0.2:3000", "node-b.lan:8080", "[fd00::2]:80", "", "10.0.0.2:3000"]) == [
        "10.0.0.2:3000", "node-b.lan:8080", "[fd00::2]:80"]
    for bad in ("10.0.0.2", "10.0.0.2:0", "10.0.0.2:70000", "a b:80", "x;y:80", "http://x:80"):
        with pytest.raises(DeployError):
            validate_backends([bad])


def test_managed_deploy_end_to_end(env):
    host, db, config, deployer, calls = env["host"], env["db"], env["config"], env["deployer"], env["calls"]
    rid, ok = deploy_once(env, SHA1)
    log = deployer.log_path("web", rid).read_text()
    assert ok, log
    release = db.get_release(rid)
    assert release["status"] == "live" and release["sha"] == SHA1
    assert db.get_app("web")["current_release"] == rid

    paths = app_paths(config, "web")
    assert host.readlink(paths.current) == f"{paths.releases}/{SHA1}"

    unit = host.read_text("/etc/systemd/system/mas-app-web.service")
    assert "User=mas-web" in unit and "next start -H 127.0.0.1 -p 7100" in unit
    assert "WorkingDirectory=/srv/mas/apps/web/current" in unit
    assert host.read_text("/etc/mas/apps/web.env") == 'API_KEY="s3cr3t"\n'

    vhost = host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    assert "server_name web.example.com;" in vhost and "proxy_pass http://127.0.0.1:7100;" in vhost
    assert host.exists("/etc/nginx/sites-enabled/mas-app-web.conf")
    assert "map $http_upgrade" in host.read_text("/etc/nginx/conf.d/mas-maps.conf")

    argvs = [a for a, _ in calls]
    assert ["runuser", "-u", "mas-web", "--", "npm", "ci", "--no-audit", "--no-fund"] in argvs
    assert ["runuser", "-u", "mas-web", "--", "npm", "run", "build"] in argvs
    assert any("certonly" in a and "web.example.com" in a for a in argvs)
    assert ["systemctl", "restart", "mas-app-web.service"] in host.executed
    assert ["nginx", "-t"] in host.executed
    # the token reaches git through the environment only, and never the log
    clone_argv, clone_env = next(c for c in calls if c[0][:2] == ["git", "clone"])
    assert "ghs_secret_token" not in " ".join(clone_argv) and "GIT_CONFIG_VALUE_0" in clone_env
    assert "ghs_secret_token" not in log and "s3cr3t" not in log
    build_env = next(e for a, e in calls if a[-2:] == ["run", "build"])
    assert build_env["API_KEY"] == "s3cr3t"


def test_first_deploy_serves_a_status_page_then_the_app(env):
    host, db, deployer = env["host"], env["db"], env["deployer"]
    written = []
    original = host.write_text

    def spy(path, content, mode=0o644):
        if path.endswith("mas-app-web.conf"):
            written.append(content)
        original(path, content, mode)

    host.write_text = spy
    rid, ok = deploy_once(env, SHA1)
    assert ok, deployer.log_path("web", rid).read_text()
    # placeholder first (before the build), then the real proxy vhost
    assert "return 503;" in written[0] and "deploying.html" in written[0]
    assert "proxy_pass http://127.0.0.1:7100;" in written[-1]
    assert "Deploying" in host.read_text("/srv/mas/pages/deploying.html")
    log = deployer.log_path("web", rid).read_text()
    assert log.index("Preparing web.example.com") < log.index("Building")


def test_failed_first_deploy_shows_unavailable_page(env):
    host, db = env["host"], env["db"]
    env["health"]["ok"] = False
    rid, ok = deploy_once(env, SHA1)
    assert not ok
    vhost = host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    assert "unavailable.html" in vhost and "proxy_pass" not in vhost


def test_failed_health_check_rolls_back(env):
    host, db, config, deployer = env["host"], env["db"], env["config"], env["deployer"]
    first, _ = deploy_once(env, SHA1)
    env["health"]["ok"] = False
    second, ok = deploy_once(env, SHA2)
    assert not ok
    assert db.get_release(second)["status"] == "failed"
    assert "health check failed" in db.get_release(second)["error"]
    assert host.readlink(app_paths(config, "web").current).endswith(SHA1)
    assert db.get_release(first)["status"] == "live"
    assert "Rolling back" in deployer.log_path("web", second).read_text()


def test_rollback_and_pruning(env):
    host, db, config, deployer = env["host"], env["db"], env["config"], env["deployer"]
    r1, _ = deploy_once(env, SHA1)
    r2, _ = deploy_once(env, SHA2)
    assert db.get_release(r1)["status"] == "superseded"

    rid = db.create_release("web", "rollback", "test", SHA1)
    db.update_release(rid, path=db.get_release(r1)["path"])
    assert deployer.run(rid)
    assert host.readlink(app_paths(config, "web").current).endswith(SHA1)
    assert db.get_release(rid)["status"] == "live" and db.get_release(r2)["status"] == "superseded"

    # keep_releases=2: the two most recent releases are SHA3 and the rollback to SHA1
    deploy_once(env, SHA3)
    assert sorted(host.listdir(app_paths(config, "web").releases)) == sorted([SHA1, SHA3])
    assert db.get_release(r2)["path"] is None


def test_nginx_test_failure_restores_and_fails(env):
    host, db = env["host"], env["db"]
    host._commands = {"nginx -t": {"returncode": 1, "stderr": "emerg: broken"}, **host._commands}
    rid, ok = deploy_once(env, SHA1)
    assert not ok and "nginx -t failed" in db.get_release(rid)["error"]
    assert not host.exists("/etc/nginx/sites-available/mas-app-web.conf")
    assert not host.exists("/etc/nginx/sites-enabled/mas-app-web.conf")
    assert ["systemctl", "reload", "nginx"] not in host.executed


def test_refuses_a_domain_served_by_another_vhost(env):
    db = env["db"]
    db.update_app("web", domain="blog.example.com")
    rid, ok = deploy_once(env, SHA1)
    assert not ok and "already served by /etc/nginx/sites-enabled/blog" in db.get_release(rid)["error"]


def test_python_app_requires_start_command(env):
    db = env["db"]
    db.update_app("web", type="python")
    rid, ok = deploy_once(env, SHA1)
    assert not ok and "start command" in db.get_release(rid)["error"]


def test_adopted_app_updates_in_place(env):
    from mas.correlator import scan

    host, db, deployer, calls = env["host"], env["db"], env["deployer"], env["calls"]
    services = {s.id: s for s in scan(host).services}
    deployer.find_service = services.get
    host._commands = {"*git -C * status --porcelain*": {"output": ""}, **host._commands}
    db.create_app("webapp", name="web", repo="acme/web", branch="production", installation_id=99, type="nextjs",
                  port=3000, mode="adopted", service_id="app.example.com", path="/home/alice/apps/web")
    env["set_sha"](SHA2)
    rid = db.create_release("webapp", "push", "github:alice", SHA2)
    assert deployer.run(rid), deployer.log_path("webapp", rid).read_text()
    argvs = [a for a, _ in calls]
    assert any(a[-3:] == ["reset", "--hard", "FETCH_HEAD"] for a in argvs)
    assert any(a[-2:] == ["restart", "web"] for a in host.executed)  # pm2 restart through the allow-list
    assert not host.exists("/srv/mas/apps/webapp")  # nothing reinstalled

    host._commands["*git -C * status --porcelain*"] = {"output": " M package.json\n"}
    rid = db.create_release("webapp", "push", "github:alice", SHA2)
    assert not deployer.run(rid)
    assert "uncommitted changes" in db.get_release(rid)["error"]


# -- API: webhook & apps ----------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands["*"] = {"returncode": 0}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    config = Config(cookie_secure=False, github_app_id=1, github_webhook_secret="hooksecret",
                    secret_key_path="/etc/mas/secret.key")
    app = create_app(host, config, db, background=False)
    queued: list = []
    app.state.deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: \
        queued.append((app_id, trigger, sha)) or 1
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    return client, db, queued, {"X-CSRF-Token": csrf}


def signed(body: dict, secret="hooksecret"):
    raw = json.dumps(body).encode()
    return raw, "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_webhook_requires_valid_signature(api):
    client, *_ = api
    raw, sig = signed({"zen": "hi"})
    assert client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "ping"}).status_code == 401
    forged = "sha256=" + "0" * 64
    r = client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": forged})
    assert r.status_code == 401
    r = client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig})
    assert r.status_code == 200 and r.json()["pong"]


def test_push_deploys_matching_apps(api):
    client, db, queued, _ = api
    db.create_app("web", name="web", repo="Acme/Web", branch="main", type="nextjs", port=7100)
    db.create_app("web-staging", name="s", repo="acme/web", branch="staging", type="nextjs", port=7101)
    db.create_app("manual", name="m", repo="acme/web", branch="main", type="nextjs", port=7102, auto_deploy=0)
    push = {"ref": "refs/heads/main", "after": SHA1, "repository": {"full_name": "acme/web"},
            "pusher": {"name": "alice"}}
    raw, sig = signed(push)
    r = client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
    assert r.json()["deployed"] == ["web"]
    assert queued == [("web", "push", SHA1)]
    raw, sig = signed({**push, "ref": "refs/tags/v1"})
    client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
    assert len(queued) == 1


def test_scaling_api(api):
    client, db, queued, h = api
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100)
    db.create_app("site", name="s", repo="acme/site", branch="main", type="static")
    r = client.put("/api/apps/web/scaling", json={"instances": 3, "backends": ["10.0.0.12:3000"]}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["instances"] == 3 and len(set(data["instance_ports"])) == 3 and data["instance_ports"][0] == 7100
    assert data["backends"] == ["10.0.0.12:3000"] and queued[-1][:2] == ("web", "manual")
    ports = data["instance_ports"]
    r = client.put("/api/apps/web/scaling", json={"instances": 2, "backends": []}, headers=h)
    assert r.json()["instance_ports"] == ports[:2]  # scaling down keeps the existing ports
    assert client.put("/api/apps/web/scaling", json={"instances": 1, "backends": ["nope"]}, headers=h).status_code == 400
    assert client.put("/api/apps/web/scaling", json={"instances": 99}, headers=h).status_code == 422
    assert client.put("/api/apps/site/scaling", json={"instances": 2}, headers=h).status_code == 400


def test_create_app_and_env_api(api):
    client, db, queued, h = api
    body = {"id": "web", "repo": "acme/web", "branch": "main", "installation_id": 9, "domain": "Web.Example.com",
            "type": "nextjs", "env": "API_KEY=abc\n"}
    r = client.post("/api/apps", json=body, headers=h)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["domain"] == "web.example.com" and 7100 <= created["port"] <= 7999
    assert "env" not in created and created["has_env"]
    assert queued == [("web", "create", None)]
    assert "abc" not in (db.get_app("web")["env"] or "")
    assert client.get("/api/apps/web/env").json()["text"] == "API_KEY=abc\n"
    assert client.post("/api/apps", json=body, headers=h).status_code == 409
    bad = {**body, "id": "py", "type": "python"}
    assert "start command" in client.post("/api/apps", json=bad, headers=h).json()["detail"]
    assert client.put("/api/apps/web/env", json={"text": "oops"}, headers=h).status_code == 400


# -- health checks (phase 2.5) ------------------------------------------------------


def _cert_pem(name: str, expires: datetime) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(1).not_valid_before(expires - timedelta(days=90)).not_valid_after(expires)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM)


def test_certificate_checks(tmp_path):
    from mas.collectors import ScanContext
    from mas.collectors import nginx as nginx_collector

    host = FixtureHost(server.build(tmp_path / "srv"))
    now = datetime.now(timezone.utc)

    def lineage(name, conf, expires):
        host.write_text(f"/etc/letsencrypt/renewal/{name}.conf", conf)
        host.write_text(f"/etc/letsencrypt/live/{name}/fullchain.pem", _cert_pem(name, expires).decode())
        host.write_text(f"/etc/letsencrypt/live/{name}/cert.pem", "x")

    host.symlinks.add("cert.pem")
    lineage("app.example.com", "authenticator = nginx\n", now + timedelta(days=80))
    lineage("mail.example.com", "authenticator = standalone\npre_hook = systemctl stop nginx\n"
                                "post_hook = systemctl start nginx\n", now + timedelta(days=70))
    lineage("old.example.com", "authenticator = webroot\nwebroot_path = /var/www/letsencrypt,\n",
            now - timedelta(days=3))
    host.write_text("/etc/letsencrypt/renewal/gone.example.com.conf", "authenticator = nginx\n")

    sites = nginx_collector.collect(ScanContext(host))
    checks = {(c.level, c.title) for c in check_certificates(host, sites, load_lineages(host), now)}
    assert ("warning", "mail.example.com: renewal stops nginx") in checks
    assert ("warning", "old.example.com: expired") in checks  # not used by nginx -> warning only
    assert ("info", "old.example.com: not used by any enabled site") in checks
    assert ("warning", "old.example.com: webroot missing") in checks
    assert ("error", "gone.example.com: renewal config without certificate") in checks
    assert not any(t.startswith("app.example.com") for _, t in checks)

    host.symlinks.discard("cert.pem")
    checks = {(c.level, c.title) for c in check_certificates(host, sites, load_lineages(host), now)}
    assert ("error", "app.example.com: broken certbot lineage") in checks


def test_nginx_outside_systemd_and_missing_cert(tmp_path):
    from mas.collectors import ScanContext
    from mas.collectors import nginx as nginx_collector
    from mas.collectors import ports

    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands = {"systemctl show nginx -p MainPID --value": {"output": "4242\n"},
                      "nginx -t": {"returncode": 0}, **host._commands}
    ctx = ScanContext(host)
    sites, sockets = nginx_collector.collect(ctx), ports.collect(ctx)
    titles = {c.title for c in check_nginx(host, sites, sockets)}
    assert "nginx runs outside systemd" in titles
    assert not any("certificate file missing" in t for t in titles)

    host.real("/etc/letsencrypt/live/blog.example.com/fullchain.pem").unlink()
    host._commands["systemctl show nginx -p MainPID --value"] = {"output": "1100\n"}
    titles = {c.title for c in check_nginx(host, sites, sockets)}
    assert "blog.example.com: certificate file missing" in titles
    assert "nginx runs outside systemd" not in titles


def test_dns_checks():
    table = {"a.example.com": ["93.184.216.34"], "b.example.com": ["93.184.216.34"],
             "moved.example.com": ["216.198.79.1"], "gone.example.com": []}
    titles = {c.title for c in check_dns(set(table) | {"*.wild.example.com"}, None, resolver=lambda n: table[n])}
    assert titles == {"moved.example.com points elsewhere", "gone.example.com does not resolve"}


def test_nextjs_static_export_is_served_by_nginx(env):
    host, db, config, deployer = env["host"], env["db"], env["config"], env["deployer"]
    served = []

    def on_stream(argv, cwd, env_vars):
        if argv[-2:] == ["run", "build"]:  # `output: 'export'` writes out/ and has no server
            host.makedirs(f"{cwd}/out")
            host.write_text(f"{cwd}/out/index.html", "<h1>hi</h1>")

    host.stream_hooks.append(on_stream)
    deployer.health_check = lambda url, h, t: (served.append((url, h)) or True, "HTTP 200")
    rid, ok = deploy_once(env, SHA1)
    log = deployer.log_path("web", rid).read_text()
    assert ok, log
    assert "static export" in log
    vhost = host.read_text("/etc/nginx/sites-available/mas-app-web.conf")
    assert "root /srv/mas/apps/web/current/out;" in vhost and "proxy_pass" not in vhost
    assert not host.exists("/etc/systemd/system/mas-app-web.service")
    assert served == [("http://127.0.0.1/", "web.example.com")]
