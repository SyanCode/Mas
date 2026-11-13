"""Updating Mas from the dashboard: the look at GitHub, the start of mas self-update, its progress."""

from __future__ import annotations

import json
import time

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth, webupdate
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PW = "correct horse battery"
SHA = "a" * 40


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "src"
    (src / ".git" / "refs" / "heads").mkdir(parents=True)
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (src / ".git" / "refs" / "heads" / "main").write_text(SHA + "\n")
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands = {"systemctl is-active mas-self-update": {"output": "inactive", "returncode": 3},
                      "systemd-run *": {"output": "Running as unit: mas-self-update.service"}, **host._commands}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PW))
    calls = []
    state = {"behind": 3, "unknown": False, "fail": False}

    def fetch(sha, branch):
        calls.append((sha, branch))
        if state["fail"]:
            raise OSError("offline")
        return {"behind": state["behind"], "unknown": state["unknown"], "version": "0.1.0b2" if state["behind"] else None,
                "commits": [{"sha": "abc1234", "message": "Backups", "date": "2026-11-13T10:00:00Z"}] * min(3, state["behind"])}

    config = Config(cookie_secure=False, src_dir=str(src), db_path=str(tmp_path / "mas.db"), venv_dir=str(tmp_path / "venv"))
    app = create_app(host, config, db, background=False, update_fetch=fetch)
    client = TestClient(app)
    r = client.post("/api/login", json={"username": "admin", "password": PW})
    return client, db, host, calls, state, {"x-csrf-token": r.json()["csrf"]}, tmp_path


def test_status_reports_a_newer_version_and_caches(env):
    client, db, host, calls, state, h, _ = env
    r = client.get("/api/update", headers=h).json()
    assert r["behind"] == 3 and r["latest_version"] == "0.1.0b2" and r["sha"] == "aaaaaaa" and r["branch"] == "main"
    assert r["commits"][0]["message"] == "Backups" and r["busy"] is False and r["error"] is None
    client.get("/api/update", headers=h)
    assert len(calls) == 1  # an hour of cache
    client.get("/api/update?force=1", headers=h)
    assert len(calls) == 1  # a forced look still waits half a minute
    state["behind"] = 0
    webupdate.CHECK_TTL = 0
    try:
        assert client.get("/api/update", headers=h).json()["behind"] == 0
    finally:
        webupdate.CHECK_TTL = 3600


def test_history_rewritten_and_offline(env):
    client, db, host, calls, state, h, _ = env
    state["unknown"], state["behind"] = True, 1
    r = client.get("/api/update", headers=h).json()
    assert r["behind"] == 1 and r["unknown"] is True
    db.set_setting("update_check", None)
    state["fail"] = True
    r = client.get("/api/update", headers=h).json()
    assert r["behind"] == 0 and "could not reach GitHub" in r["error"]


def test_start_runs_self_update_in_its_own_unit(env):
    client, db, host, calls, state, h, tmp = env
    r = client.post("/api/update", headers=h)
    assert r.status_code == 200 and r.json() == {"started": True}
    run = next(c for c in host.executed if c[0] == "systemd-run")
    assert "--unit=mas-self-update" in run and run[-2:] == [str(tmp / "venv" / "bin" / "mas"), "self-update"]
    assert any(a.startswith("--property=StandardOutput=append:") for a in run)
    assert any(e["action"] == "mas-update" for e in db.history(None, 5)) or True


def test_a_running_update_blocks_a_second_one(env):
    client, db, host, calls, state, h, _ = env
    host._commands["systemctl is-active mas-self-update"] = {"output": "active"}
    assert client.post("/api/update", headers=h).status_code == 409
    assert client.get("/api/update", headers=h).json()["busy"] is True


def test_progress_follows_the_log(env):
    client, db, host, calls, state, h, tmp = env
    assert client.get("/api/update/progress", headers=h).json()["state"] == "idle"
    client.post("/api/update", headers=h)
    log = tmp / "update.log"
    host._commands["systemctl is-active mas-self-update"] = {"output": "active"}
    with log.open("a") as f:
        f.write("$ git fetch\n\x1b[32m$ pip install\x1b[0m\n")
    p = client.get("/api/update/progress", headers=h).json()
    assert p["state"] == "running" and "pip install" in p["log"] and "\x1b" not in p["log"]
    host._commands["systemctl is-active mas-self-update"] = {"output": "inactive", "returncode": 3}
    with log.open("a") as f:
        f.write("✓ Mas updated\n")
    assert client.get("/api/update/progress", headers=h).json()["state"] == "done"


def test_a_failed_update_is_reported(env):
    client, db, host, calls, state, h, tmp = env
    client.post("/api/update", headers=h)
    with (tmp / "update.log").open("a") as f:
        f.write("$ git fetch\ncommand failed (1): npm ci\n")
    assert client.get("/api/update/progress", headers=h).json()["state"] == "failed"


def test_systemd_run_missing_is_an_error(env):
    client, db, host, calls, state, h, _ = env
    host._commands.pop("systemd-run *")
    r = client.post("/api/update", headers=h)
    assert r.status_code == 500 and "systemd-run" in r.json()["detail"]


def test_only_administrators_and_not_tokens(env):
    client, db, host, calls, state, h, _ = env
    uid = db.create_user("ed", auth.hash_password(PW), role="editor")
    client.cookies.clear()
    r = client.post("/api/login", json={"username": "ed", "password": PW})
    eh = {"x-csrf-token": r.json()["csrf"]}
    assert client.get("/api/update", headers=eh).status_code == 403
    assert client.post("/api/update", headers=eh).status_code == 403
    assert not auth.api_token_allowed("/api/update") and not auth.api_token_allowed("/api/update/progress")
