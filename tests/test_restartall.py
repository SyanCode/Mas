"""Restart every service at once: nothing recreated, the same configuration before and after."""

from __future__ import annotations

import json

import server

from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer
from mas.host import FixtureHost
from mas.restartall import RestartAll, container_fingerprint
from mas.state import Scanner

MAIL = {"Id": "a" * 64, "Image": "sha256:poste", "Config": {"Env": ["HTTPS=OFF", "TZ=Europe/Paris"], "Cmd": None},
        "HostConfig": {"PortBindings": {"25/tcp": [{"HostPort": "25"}]}, "RestartPolicy": {"Name": "unless-stopped"}},
        "Mounts": [{"Source": "/srv/mas/stacks/mail/data", "Destination": "/data", "RW": True}],
        "NetworkSettings": {"Networks": {"mas-mail_default": {}}}, "State": {"Running": True, "Status": "running"}}


def make(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    config = Config()
    deployer = Deployer(host, config, db, lambda: None, health_check=lambda url, h, t: (True, "200"))
    db.create_app("site", name="Site", repo="acme/site", branch="main", type="node", port=3100)
    db.update_app("site", mode="managed")
    running = [{"Id": "a" * 64, "Names": ["/mas-mail-mail-1"],
                "Labels": {"com.docker.compose.project": "mas-mail"}},
               {"Id": "b" * 64, "Names": ["/mas-supabase-kong"],
                "Labels": {"com.docker.compose.project": "mas-supabase", "com.docker.compose.depends_on": "db:x:false"}},
               {"Id": "c" * 64, "Names": ["/mas-supabase-db"], "Labels": {"com.docker.compose.project": "mas-supabase"}}]
    host._docker_json = {"/containers/json": running, **{f"/containers/{x * 64}/json": dict(MAIL, Id=x * 64) for x in "abc"}}
    real_get = host.docker_get
    host.docker_get = lambda path: host._docker_json[path] if path in host._docker_json else real_get(path)
    host._commands = {"systemctl is-active *": {"output": "active\n"}, "systemctl restart *": {"output": ""},
                      "nginx -t": {"output": ""}, "systemctl reload nginx": {"output": ""}, **host._commands}
    restart = RestartAll(host, config, db, deployer, Scanner(host, config, db))
    restart.sleep = lambda s: None
    return host, db, restart


def test_plan_order_and_what_is_left_alone(tmp_path):
    _, _, restart = make(tmp_path)
    keys = [i["key"] for i in restart.plan()]
    containers = [k for k in keys if k.startswith("docker:")]
    # the project's containers others depend on come first (db before kong)
    assert containers == ["docker:" + "a" * 64, "docker:" + "c" * 64, "docker:" + "b" * 64]
    assert "unit:api.service" in keys and "unit:mariadb.service" in keys and "pm2:alice:web" in keys
    assert "app:mas-app-site.service" in keys and keys[-1] == "nginx"
    assert not any(k in keys for k in ("unit:nginx.service", "unit:docker.service", "unit:mas.service"))
    assert "pm2:alice:report" not in keys  # stopped: stays stopped


def test_restart_is_identical_and_never_recreates(tmp_path):
    host, db, restart = make(tmp_path)
    restart.state = {"running": True, "by": "admin", "items": [i for i in restart.plan() if i["kind"] != "pm2"]}
    restart.run()
    items = restart.state["items"]
    assert all(i["status"] == "identical" for i in items), [(i["key"], i["status"], i["detail"]) for i in items]
    api_calls = [c for c in host.executed if c[0] == "docker-api"]
    assert ["docker-api", "POST", f"/containers/{'a' * 64}/restart?t=20"] in api_calls
    assert not any("compose" in c or "create" in " ".join(c) for c in host.executed)
    assert ["systemctl", "restart", "mas-app-site.service"] in host.executed
    assert not restart.state["running"]


def test_a_changed_container_is_reported(tmp_path):
    host, _, restart = make(tmp_path)
    before = container_fingerprint(MAIL)
    changed = json.loads(json.dumps(MAIL))
    changed["Config"]["Env"].append("NEW=1")
    assert container_fingerprint(changed)["env"] != before["env"]
    calls = {"n": 0}

    def inspect(path):
        calls["n"] += 1
        return MAIL if calls["n"] == 1 else changed
    host.docker_get = inspect
    status, detail = restart._restart_container("a" * 64)
    assert status == "changed" and "env" in detail


def test_a_new_pid_is_not_a_change(tmp_path):
    """systemctl show changes with every start (pid, start time): only the unit files count."""
    host, _, restart = make(tmp_path)
    starts = iter(range(100, 200))

    def run(argv, timeout=None):
        from mas.host import CommandResult
        if argv[:2] == ["systemctl", "show"]:
            out = "FragmentPath=/lib/systemd/system/mariadb.service\n"
            if "ExecStart" in argv:  # what systemd prints: the running process, new at every start
                out += f"ExecStart={{ path=/usr/sbin/mariadbd ; pid={next(starts)} ; start_time=[...] }}\n"
            return CommandResult(0, out, "")
        if argv[:2] == ["systemctl", "restart"]:
            return CommandResult(0, "", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return CommandResult(0, "active\n", "")
        return None
    host.run = run
    host.write_text("/lib/systemd/system/mariadb.service", "[Service]\nExecStart=/usr/sbin/mariadbd\n")
    assert restart._restart_unit("mariadb.service")[0] == "identical"
