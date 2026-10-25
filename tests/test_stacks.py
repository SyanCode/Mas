"""Ready-made services: catalog, checks, compose files, deploy, mail setup, removal."""

from __future__ import annotations

import json

import pytest
import server
import yaml
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.deploy import ReleaseLog
from mas.host import FixtureHost
from mas.stacks import TEMPLATES, Ctx, StackError, check_options, dkim_record

PASSWORD = "correct horse battery"
RUNNING = '[{"Service": "minecraft", "State": "running", "Health": ""}]'


def ctx(template: str, options: dict, **kw) -> Ctx:
    return Ctx(kw.get("id", "x"), check_options(TEMPLATES[template], options), kw.get("secrets", {}),
               "/srv/mas/stacks/x/data", "/srv/mas/stacks/x/data", kw.get("web_port"), kw.get("ports", {}),
               kw.get("domain"))


def test_options_are_checked():
    mc = TEMPLATES["minecraft"]
    with pytest.raises(StackError, match="EULA"):
        check_options(mc, {})
    opts = check_options(mc, {"eula": True, "max_players": 20, "unknown": "x"})
    assert opts["server_type"] == "PAPER" and opts["max_players"] == 20 and "unknown" not in opts
    for bad in ({"eula": True, "max_players": 0}, {"eula": True, "mode": "god"}, {"eula": True, "motd": "a\nb"},
                {"eula": True, "max_players": "10"}):
        with pytest.raises(StackError):
            check_options(mc, bad)
    mail = TEMPLATES["mail"]
    assert check_options(mail, {"mail_domain": "Example.COM", "mailbox": "Contact"})["mail_domain"] == "example.com"
    for bad in ({"mail_domain": "nope"}, {"mail_domain": "example.com", "mailbox": "a b"}):
        with pytest.raises(StackError):
            check_options(mail, bad)
    custom = TEMPLATES["custom"]
    assert check_options(custom, {"image": "ghcr.io/owner/app:1.2", "public_ports": "25565:25565, 19132:19132/udp",
                                  "env": "A=1\n# note\nB=two words"})["image"] == "ghcr.io/owner/app:1.2"
    for bad in ({"image": "Bad Image"}, {"image": "nginx", "public_ports": "80"}, {"image": "nginx", "env": "1A=x"},
                {"image": "nginx", "volume": "data"}, {"image": "nginx;rm -rf /"}):
        with pytest.raises(StackError):
            check_options(custom, bad)


def test_compose_files():
    mc = TEMPLATES["minecraft"].compose(ctx("minecraft", {"eula": True, "whitelist": "Alice,Bob"},
                                            ports={"game": 25570}))["services"]["minecraft"]
    assert mc["ports"] == ["25570:25565/tcp"] and mc["environment"]["EULA"] == "TRUE"
    assert mc["environment"]["WHITELIST"] == "Alice,Bob" and mc["volumes"] == ["/srv/mas/stacks/x/data:/data"]
    kuma = TEMPLATES["uptime-kuma"].compose(ctx("uptime-kuma", {}, web_port=7105, domain="up.example.com"))
    assert kuma["services"]["uptime-kuma"]["ports"] == ["127.0.0.1:7105:3001"]  # nginx only
    assert kuma["services"]["uptime-kuma"]["logging"]["options"]["max-size"] == "10m"  # Docker logs rotated
    mail = TEMPLATES["mail"].compose(ctx("mail", {"mail_domain": "example.com"}, domain="mail.example.com",
                                         ports={"smtp": 25, "submissions": 465, "submission": 587, "imaps": 993}))
    svc = mail["services"]["mailserver"]
    assert svc["hostname"] == "mail.example.com" and "25:25" in svc["ports"]
    assert "/etc/letsencrypt:/etc/letsencrypt:ro" in svc["volumes"] and svc["environment"]["SSL_TYPE"] == "letsencrypt"
    custom = TEMPLATES["custom"].compose(ctx("custom", {"image": "nginx:alpine", "web_port": 80,
                                                        "public_ports": "2222:22"}, web_port=7106))
    assert custom["services"]["app"]["ports"] == ["127.0.0.1:7106:80", "2222:22"]
    assert "environment" not in custom["services"]["app"]


def test_catalog_texts_are_listed_for_translation():
    from pathlib import Path

    from mas.stacks import ui_texts
    listed = (Path(__file__).resolve().parent.parent / "web" / "lib" / "stackTexts.ts").read_text(encoding="utf-8")
    missing = [t for t in ui_texts() if f"msg({json.dumps(t, ensure_ascii=False)})" not in listed]
    assert not missing, f"run `python -m mas.stacks > web/lib/stackTexts.ts`: {missing[:5]}"


def test_data_lives_in_the_data_folder():
    """What every template keeps is in /srv/mas/stacks/<name>/data (backups and the docs rely on it)."""
    from mas.stacks import TEMPLATES
    for t in TEMPLATES.values():
        options = {f["key"]: f.get("default") for f in t.fields}
        options.update({"eula": True, "mail_domain": "e2e.test", "image": "nginx", "volume": "/data"})
        c = Ctx("x", options, {"admin_token": "t", "mailbox_password": "p"}, "/srv/mas/stacks/x/data",
                "/srv/mas/stacks/x/data", 7100, {p.name: p.container for p in t.ports}, "x.example.com")
        for service in t.compose(c)["services"].values():
            for volume in service.get("volumes", []):
                source = volume.split(":")[0]
                if source.startswith("/srv/mas/stacks/"):
                    assert source == c.data or source.rsplit("/", 1)[0] == c.data, (t.id, volume)
                    assert not source.startswith(f"{c.data}/data"), (t.id, volume)


def test_dkim_record():
    text = ('mail._domainkey\tIN\tTXT\t( "v=DKIM1; h=sha256; k=rsa; "\n\t  "p=MIIBIjANBg"\n\t  "kqhkiG9w0" )'
            '  ; ----- DKIM key mail for example.com\n')
    assert dkim_record(text) == "v=DKIM1; h=sha256; k=rsa; p=MIIBIjANBgkqhkiG9w0"


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    deployer = app.state.deployer
    deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: db.create_release(
        app_id, trigger, actor, sha)
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]
    return client, host, db, deployer, {"X-CSRF-Token": csrf}


def test_minecraft_server(env):
    client, host, db, deployer, h = env
    names = [t["id"] for t in client.get("/api/stacks/templates").json()]
    assert {"minecraft", "mail", "uptime-kuma", "custom"} <= set(names)
    body = {"template": "minecraft", "id": "mc", "options": {"eula": True, "memory": "4G"}, "ports": {"game": 25565}}
    assert client.post("/api/stacks", json={**body, "options": {}}, headers=h).status_code == 400  # no EULA
    assert client.post("/api/stacks", json={**body, "ports": {"game": 22}}, headers=h).status_code == 400
    r = client.post("/api/stacks", json=body, headers=h)
    assert r.status_code == 200, r.text
    app = db.get_app("mc")
    assert app["mode"] == "stack" and app["port"] is None and app["repo"] == ""
    taken = client.post("/api/stacks", json={**body, "id": "mc2"}, headers=h)
    assert taken.status_code == 400 and "25565" in taken.json()["detail"]  # same port twice

    host._commands["docker compose -p mas-mc * pull"] = {"output": "Pulled"}
    host._commands["docker compose -p mas-mc * up -d --remove-orphans"] = {"output": "Started"}
    host._commands["docker compose -p mas-mc * ps --all --format json"] = {"output": RUNNING}
    rid = r.json()["release_id"]
    assert deployer.run(rid), deployer.log_path("mc", rid).read_text()
    compose = yaml.safe_load(host.read_text("/srv/mas/stacks/mc/docker-compose.yml"))
    env = compose["services"]["minecraft"]["environment"]
    assert env["MAX_MEMORY"] == "4G" and env["INIT_MEMORY"] == "512M" and "MEMORY" not in env  # grows when needed
    assert db.get_app("mc")["current_release"] == rid
    info = client.get("/api/apps/mc/stack").json()
    assert info["template"]["id"] == "minecraft" and info["rows"][0]["label"] == "Server address"
    assert client.get("/api/apps/mc").json()["stack"]["template"] == "minecraft"

    # settings: new options, the others kept, then a redeploy
    r = client.put("/api/apps/mc/stack", json={"options": {"difficulty": "hard"}}, headers=h)
    assert r.status_code == 200
    options = json.loads(db.get_app("mc")["stack"])["options"]
    assert options["difficulty"] == "hard" and options["memory"] == "4G"

    # a container that dies right away fails the deploy, with its logs
    host._commands["docker compose -p mas-mc * ps --all --format json"] = {
        "output": '{"Service": "minecraft", "State": "exited"}\n'}
    host._commands["docker compose -p mas-mc * logs *"] = {"output": "Failed to bind to port"}
    assert not deployer.run(r.json()["release_id"])
    assert "Failed to bind" in db.get_release(r.json()["release_id"])["error"]

    # controls and removal
    host._commands["docker compose -p mas-mc * restart"] = {"output": ""}
    assert client.post("/api/apps/mc/stack/restart", headers=h).status_code == 200
    assert client.post("/api/apps/mc/stack/reboot", headers=h).status_code == 400
    host._commands["docker compose -p mas-mc * down --remove-orphans"] = {"output": "Removed"}
    r = client.delete("/api/apps/mc", params={"purge": True}, headers=h)
    assert r.status_code == 200 and db.get_app("mc") is None
    assert not host.exists("/srv/mas/stacks/mc")


def test_mail_server_setup(env, tmp_path):
    client, host, db, deployer, h = env
    body = {"template": "mail", "id": "mail", "domain": "mail.example.com",
            "options": {"mail_domain": "example.com", "mailbox": "contact"}}
    assert client.post("/api/stacks", json={**body, "domain": None}, headers=h).status_code == 400
    assert client.post("/api/stacks", json=body, headers=h).status_code == 200
    stacks = client.app.state.stacks
    app = db.get_app("mail")
    ctx = stacks.ctx(app)
    assert ctx.ports == {"smtp": 25, "submissions": 465, "submission": 587, "imaps": 993}
    assert len(ctx.secrets["mailbox_password"]) >= 16

    calls: list[list[str]] = []
    stacks.exec = lambda a, service, args, retries=1: calls.append(args) or ""
    stacks.compose = lambda a, args, rlog=None, timeout=900: calls.append(["compose", *args]) or ""
    stacks._wait = lambda a, rlog, timeout=180, healthy=False: calls.append(["wait", healthy])
    TEMPLATES["mail"].after(stacks, app, ctx, ReleaseLog(tmp_path / "m.log"))
    assert calls[0] == ["setup", "email", "add", "contact@example.com", ctx.secrets["mailbox_password"]]
    assert calls[1] == ["setup", "config", "dkim", "domain", "example.com"] and calls[2] == ["compose", "restart"]
    assert calls[3] == ["wait", True]  # the deploy ends once the server is healthy (IMAP listening)
    assert calls[4] == ["doveadm", "auth", "test", "contact@example.com", ctx.secrets["mailbox_password"]]

    host.makedirs("/srv/mas/stacks/mail/data/config/opendkim/keys/example.com")
    host.write_text("/srv/mas/stacks/mail/data/config/opendkim/keys/example.com/mail.txt",
                    'mail._domainkey IN TXT ( "v=DKIM1; k=rsa; " "p=ABC" )')
    host.write_text("/srv/mas/stacks/mail/data/config/postfix-accounts.cf", "contact@example.com|{SHA512-CRYPT}x")
    calls.clear()
    TEMPLATES["mail"].after(stacks, app, ctx, ReleaseLog(tmp_path / "m2.log"))
    assert [c[0] for c in calls] == ["wait", "doveadm"]  # already set up: only waits for it
    info = client.get("/api/apps/mail/stack").json()
    records = {(r["type"], r["name"]): r["value"] for r in info["dns"]}
    assert records[("MX", "example.com")] == "10 mail.example.com"
    assert records[("TXT", "mail._domainkey.example.com")] == "v=DKIM1; k=rsa; p=ABC"
    assert any(r.get("secret") for r in info["rows"] if r["label"] == "Password")

    r = client.post("/api/apps/mail/stack/actions/add-mailbox", json={"values": {"name": "Sales"}}, headers=h)
    assert r.status_code == 200 and r.json()["output"].startswith("sales@example.com created")
    assert calls[-2][:4] == ["setup", "email", "add", "sales@example.com"]
    assert calls[-1][:4] == ["doveadm", "auth", "test", "sales@example.com"]  # usable when shown
    bad = client.post("/api/apps/mail/stack/actions/add-mailbox", json={"values": {"name": "a b"}}, headers=h)
    assert bad.status_code == 400
    assert client.post("/api/apps/mail/stack/actions/nope", json={}, headers=h).status_code == 400
