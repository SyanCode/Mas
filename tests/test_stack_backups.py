"""Backups of the ready-made services: real tar archives in a temporary folder, Docker simulated."""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import Host
from mas.stack_backups import BackupError

PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path):
    host = Host(tmp_path / "root")  # real files and a real tar, under a temporary root
    db = Database(tmp_path / "var" / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False, secret_key_path="/etc/mas/secret.key"), db, background=False)
    stacks, backups = app.state.stacks, app.state.stack_backups
    calls: list[list[str]] = []
    state = {"running": True}
    stacks.compose = lambda a, args, rlog=None, timeout=900: calls.append(args) or ""
    backups._running = lambda a: state["running"]
    db.create_app("mc", name="Minecraft server (Java)", repo="", branch="", type="unknown", mode="stack",
                  stack='{"template": "minecraft", "options": {}, "ports": {"game": 25565}}')
    host.makedirs("/srv/mas/stacks/mc/data/world")
    host.write_text("/srv/mas/stacks/mc/data/world/level.dat", "day 1")
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]
    return client, host, db, backups, calls, state, {"X-CSRF-Token": csrf}


def test_backup_stops_copies_and_restarts(env):
    client, host, db, backups, calls, state, h = env
    name = backups.backup("mc")
    assert calls == [["stop"], ["start"]]  # a consistent copy: nothing writes meanwhile
    assert (backups.dir("mc") / name).stat().st_size > 0
    calls.clear()
    state["running"] = False
    time.sleep(1.1)  # names are per second
    backups.backup("mc")
    assert calls == []  # a stopped service stays stopped
    assert [b["name"] for b in backups.list("mc")][1] == name  # newest first

    backups.keep = 2
    time.sleep(1.1)
    backups.backup("mc")
    assert len(backups.list("mc")) == 2  # the oldest goes

    host.remove("/srv/mas/stacks/mc/data")
    with pytest.raises(BackupError, match="no data yet"):
        backups.backup("mc")


def test_restore_keeps_the_current_data_aside(env):
    client, host, db, backups, calls, state, h = env
    name = backups.backup("mc")
    host.write_text("/srv/mas/stacks/mc/data/world/level.dat", "day 2, griefed")
    calls.clear()
    out = backups.restore("mc", name)
    assert host.read_text("/srv/mas/stacks/mc/data/world/level.dat") == "day 1"
    assert host.read_text("/srv/mas/stacks/mc/data.before-restore/world/level.dat") == "day 2, griefed"
    assert calls == [["stop"], ["up", "-d"]] and "data.before-restore" in out

    for bad in ("../../etc/passwd", "other-20260101-000000.tar.gz", "mc-20260101-000000.tar.gz"):
        with pytest.raises(BackupError, match="unknown backup"):
            backups.restore("mc", bad)

    # a broken archive: nothing lost, the data is put back as it was
    broken = backups.dir("mc") / "mc-20200101-000000.tar.gz"
    broken.write_bytes(b"not a tar archive")
    with pytest.raises(BackupError):
        backups.restore("mc", broken.name)
    assert host.read_text("/srv/mas/stacks/mc/data/world/level.dat") == "day 1"


def test_nightly_backup(env):
    client, host, db, backups, calls, state, h = env
    assert backups.auto_backup(datetime(2026, 10, 21, 14, 0)) == []  # not at night
    assert backups.auto_backup(datetime(2026, 10, 21, 3, 30)) == []  # never deployed
    db.update_app("mc", current_release=db.create_release("mc", "create", "admin"))
    assert len(backups.auto_backup(datetime(2026, 10, 21, 3, 30))) == 1
    assert backups.auto_backup(datetime(2026, 10, 21, 4, 30)) == []  # once a night
    time.sleep(1.1)
    assert len(backups.auto_backup(datetime(2026, 10, 22, 3, 5))) == 1
    assert any(e["action"] == "stack-backup" and e["detail"] == "nightly" for e in db.history(None, 20))


def test_backups_from_the_dashboard(env):
    client, host, db, backups, calls, state, h = env

    def wait_job() -> dict:
        for _ in range(200):
            job = client.get("/api/apps/mc/stack/backups").json()["job"]
            if job and not job["running"]:
                return job
            time.sleep(0.05)
        raise AssertionError("the job never ended")

    r = client.post("/api/apps/mc/stack/backups", headers=h)
    assert r.status_code == 200 and r.json()["job"]["op"] == "backup"
    job = wait_job()
    assert job["error"] is None
    listed = client.get("/api/apps/mc/stack/backups").json()
    assert listed["backups"][0]["name"] == job["result"] and listed["keep"] == 7

    host.write_text("/srv/mas/stacks/mc/data/world/level.dat", "changed")
    r = client.post(f"/api/apps/mc/stack/backups/{job['result']}/restore", headers=h)
    assert r.status_code == 200
    assert wait_job()["error"] is None
    assert host.read_text("/srv/mas/stacks/mc/data/world/level.dat") == "day 1"
    assert client.post("/api/apps/mc/stack/backups/nope/restore", headers=h).status_code == 400
    assert client.get("/api/apps/web/stack/backups").status_code == 404
    assert any(e["action"] == "stack-restore" and e["ok"] for e in db.history(None, 20))
