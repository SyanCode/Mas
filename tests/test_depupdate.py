"""Automatic upgrade of vulnerable dependencies: commit, deploy, undone when the release fails."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.depaudit import OSV, DepAudit
from mas.depupdate import DepUpdater, bump_requirements, plan
from mas.host import FixtureHost

from test_depaudit import LOCK, FakeOSV

PKG = {"name": "site", "dependencies": {"next": "^16.4.0"}}


class FakeGitHub:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.commits: list[tuple[str, dict, str]] = []  # (branch, files, message)
        self.history = {"base": dict(files)}
        self.heads = {"main": "base"}
        self.pulls: list[dict] = []
        self.pull = "open"

    def head(self, inst, repo, branch):
        return self.heads[branch]

    def read_file(self, inst, repo, path, ref):
        return self.history[ref].get(path), "blob"

    def commit_files(self, inst, repo, branch, files, message, parent):
        assert self.heads[branch] == parent, "branch moved"
        sha = f"c{len(self.commits) + 1}"
        tree = dict(self.history[parent])
        for p, c in files.items():
            if c is None:
                tree.pop(p, None)
            else:
                tree[p] = c
        self.history[sha] = tree
        self.heads[branch] = sha
        self.commits.append((branch, files, message))
        return sha

    def create_branch(self, inst, repo, branch, sha):
        self.heads[branch] = sha

    def open_pull(self, inst, repo, head, base, title, body):
        self.pulls.append({"head": head, "base": base, "title": title})
        return {"number": 7, "url": "https://github.com/me/site/pull/7"}

    def pull_state(self, inst, repo, number):
        return self.pull


def fake_npm(calls: list, fail: bool = False, stuck: bool = False):
    """npm --package-lock-only: bumps next to 16.4.6 and lodash to 4.17.21 in the lock of the folder."""
    def run(argv, workdir):
        calls.append(argv)
        assert "--ignore-scripts" in argv and "--package-lock-only" in argv
        if fail:
            return 1, "npm ERR! ERESOLVE"
        lock = json.loads((Path(workdir) / "package-lock.json").read_text())
        pkg = json.loads((Path(workdir) / "package.json").read_text())
        if argv[1] == "install" and not stuck:
            lock["packages"]["node_modules/next"]["version"] = "16.4.6"
            pkg["dependencies"]["next"] = "^16.4.5"
        if argv[1] == "update" and "lodash" in argv and not stuck:
            lock["packages"]["node_modules/lodash"]["version"] = "4.17.21"
        (Path(workdir) / "package-lock.json").write_text(json.dumps(lock))
        (Path(workdir) / "package.json").write_text(json.dumps(pkg))
        return 0, "updated"
    return run


class FakeDeployer:
    def __init__(self) -> None:
        self.queued = []

    def enqueue(self, app_id, trigger, actor, sha=None):
        self.queued.append((app_id, sha))
        return 1

    def _tool_path(self):
        return "/usr/bin"


def setup(tmp_path, auto_deploy=1, **npm):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_app("site", name="site", repo="me/site", branch="main", domain="site.example", type="nextjs",
                  mode="managed", installation_id=1, auto_deploy=auto_deploy)
    rid = db.create_release("site", "push", "me", "base", status="live")
    db.update_release(rid, finished_at="2020-01-01T00:00:00+00:00")
    folder = host.real("/srv/mas/apps/site/current")
    folder.mkdir(parents=True)
    (folder / "package-lock.json").write_text(json.dumps(LOCK))
    (folder / "package.json").write_text(json.dumps(PKG))
    audit = DepAudit(db, host, Config(), OSV(FakeOSV()))
    audit.run()
    gh = FakeGitHub({"package.json": json.dumps(PKG).encode(), "package-lock.json": json.dumps(LOCK).encode()})
    calls: list = []
    deployer = FakeDeployer()
    up = DepUpdater(db, host, Config(), lambda: gh, deployer, audit, runner=fake_npm(calls, **npm))
    return db, up, gh, calls, deployer


def test_plan_keeps_majors_and_unfixed_for_a_person():
    findings = [
        {"ecosystem": "npm", "name": "next", "version": "16.4.2", "direct": True, "upgrade_to": "16.4.5"},
        {"ecosystem": "npm", "name": "old", "version": "1.0.0", "direct": True, "upgrade_to": "2.0.0"},
        {"ecosystem": "npm", "name": "nofix", "version": "1.0.0", "direct": False, "upgrade_to": None},
        {"ecosystem": "Packagist", "name": "a/b", "version": "1.0.0", "direct": True, "upgrade_to": "1.0.1"},
    ]
    todo, manual = plan(findings, {}, time.time())
    assert [t["name"] for t in todo] == ["next"]
    assert {m["name"]: m["why"] for m in manual}["old"].startswith("the fix is in a new major")
    todo, _ = plan(findings, {"npm:next@16.4.5": time.time()}, time.time())
    assert todo == []  # failed recently: not retried before a week


def test_requirements_bump():
    text = "Django==4.2.0  # web\ndjango-extensions==3.0\nrequests==2.0.0\n"
    out = bump_requirements(text, [{"name": "django", "version": "4.2.0", "to": "4.2.17"}])
    assert out == "Django==4.2.17  # web\ndjango-extensions==3.0\nrequests==2.0.0\n"


def test_push_commits_the_new_lock_and_waits_for_the_release(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path)
    rec = up.update(db.get_app("site"))
    assert rec["state"] == "deploying" and rec["sha"] == "c1"
    assert {t["name"]: t["now"] for t in rec["done"]} == {"next": "16.4.6", "lodash": "4.17.21"}
    [(branch, files, message)] = gh.commits
    assert branch == "main" and set(files) == {"package.json", "package-lock.json"}
    assert message.startswith("Update vulnerable dependencies: next 16.4.2 → 16.4.6")
    assert calls[0][:3] == ["npm", "install", "next@^16.4.5"] and calls[1][:3] == ["npm", "update", "lodash"]
    assert deployer.queued == []  # the app deploys on push: the webhook does it
    rid = db.create_release("site", "push", "github", "c1", status="live")
    assert up.watch() == ["site"]
    assert up.state("site")["state"] == "done" and up.state("site")["release_id"] == rid


def test_no_webhook_mas_deploys_itself(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path, auto_deploy=0)
    up.update(db.get_app("site"))
    assert deployer.queued == [("site", "c1")]


def test_failed_release_puts_the_files_back(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path)
    up.update(db.get_app("site"))
    rid = db.create_release("site", "push", "github", "c1", status="failed")
    db.update_release(rid, error="health check failed")
    up.watch()
    rec = up.state("site")
    assert rec["state"] == "reverted" and "health check failed" in rec["error"]
    assert gh.history[gh.heads["main"]] == gh.history["base"]  # files as before the upgrade
    assert "npm:next@16.4.5" in rec["blocked"]
    # the next audit does not start the same upgrade again
    db.set_setting("deps:update:site", json.dumps({**rec, "audit_at": None}))
    assert up.tick() == [] and len(gh.commits) == 2


def test_revert_refused_when_someone_changed_the_files(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path)
    up.update(db.get_app("site"))
    gh.commit_files(1, "me/site", "main", {"package.json": b"{}"}, "by hand", "c1")
    db.create_release("site", "push", "github", "c1", status="failed")
    up.watch()
    rec = up.state("site")
    assert rec["state"] == "failed" and "changed since the upgrade" in rec["error"]


def test_npm_failure_is_recorded_and_not_retried(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path, fail=True)
    rec = up.update(db.get_app("site"))
    assert rec["state"] == "failed" and "ERESOLVE" in rec["error"] and gh.commits == []
    assert "npm:next@16.4.5" in rec["blocked"]


def test_version_still_vulnerable_is_not_committed(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path, stuck=True)
    rec = up.update(db.get_app("site"))
    assert rec["state"] == "failed" and gh.commits == []


def test_pull_request_mode(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path)
    up.configure("pr")
    rec = up.update(db.get_app("site"))
    assert rec["state"] == "pr" and rec["pr"]["number"] == 7
    assert gh.heads["main"] == "base" and gh.commits[0][0].startswith("mas/deps-")
    assert gh.pulls[0]["base"] == "main"
    gh.pull = "closed"
    up.watch()
    assert up.state("site")["state"] == "idle"


def test_tick_upgrades_once_per_audit(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path)
    assert up.tick() == ["site"] and len(gh.commits) == 1
    assert up.tick() == []  # deploying
    up.configure("off")
    db.set_setting("deps:update:site", json.dumps({"state": "idle"}))
    assert up.tick() == []


def test_off_mode_by_hand_opens_a_pull_request(tmp_path):
    db, up, gh, calls, deployer = setup(tmp_path)
    up.configure("off")
    assert up.update(db.get_app("site"), trigger="manual")["state"] == "pr"


def test_api_settings_and_update(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    app = create_app(host, Config(cookie_secure=False), db, background=False, osv_fetch=FakeOSV(), dep_runner=fake_npm([]))
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    h = {"X-CSRF-Token": csrf}
    assert client.get("/api/dependencies").json()["settings"]["auto"] == "push"
    r = client.put("/api/dependencies/settings", headers=h, json={"auto": "pr"})
    assert r.status_code == 200 and r.json()["auto"] == "pr"
    assert client.put("/api/dependencies/settings", headers=h, json={"auto": "yes"}).status_code == 400
    db.create_app("site", name="site", repo="me/site", branch="main", type="nextjs", mode="managed")
    r = client.post("/api/dependencies/update?app_id=site", headers=h)
    assert r.status_code == 409 and "GitHub" in r.json()["detail"]
