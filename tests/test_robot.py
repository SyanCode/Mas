"""The robot: tool loop, approvals, guarded paths, deployment, short-lived token."""

from __future__ import annotations

import asyncio
import io
import json

import pytest
import server

from mas import auth
from mas.ai import AiError, AiPool
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost
from mas.robot import PRESETS, Robot, safe_path

API = "sk-ant-api03-" + "b" * 40


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def tool(name, **args):
    return {"type": "tool_use", "id": f"id-{name}", "name": name, "input": args}


def script(*turns):
    """An opener answering with these turns (lists of blocks), one per request; keeps the requests."""
    seen = []
    queue = list(turns)

    def opener(req, timeout):
        seen.append(json.loads(req.data))
        blocks = queue.pop(0)
        return Resp(json.dumps({"content": blocks, "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"}).encode())
    opener.seen = seen
    return opener


class FakeMas:
    def __init__(self, releases=("live",)):
        self.calls, self.releases = [], list(releases)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, path, **kw):
        self.calls.append(("GET", path))
        if path == "/api/apps":
            return [{"id": "shop", "domain": "shop.example.com", "repo": "acme/shop", "branch": "main", "type": "static"}]
        if path == "/api/apps/shop":
            return {"releases": [{"id": 7, "status": self.releases.pop(0) if len(self.releases) > 1 else self.releases[0]}]}
        if path.endswith("/compliance"):
            return {"latest": {"score": 40, "verdict": "blocking", "counts": {}, "findings": [
                {"level": "critical", "title": "No cookie banner", "detail": "d", "fix": "add one"}]}}
        raise AssertionError(path)

    async def post(self, path, **kw):
        self.calls.append(("POST", path))
        if path.endswith("/deploy"):
            return {"release_id": 7}
        if path.endswith("/seo/optimize"):
            return {"applied": ["robots", "sitemap"]}
        raise AssertionError(path)

    async def request(self, method, path, **kw):
        return "build log"


class FakeGitHub:
    def __init__(self):
        self.commits = []

    def head(self, inst, repo, branch):
        return "h" * 40

    def tree(self, inst, repo, ref):
        return [{"path": "index.html", "size": 10}, {"path": ".github/workflows/x.yml", "size": 5}]

    def read_file(self, inst, repo, path, ref):
        return (b"<html><title>Old</title><p>x</p><p>x</p></html>", "sha") if path == "index.html" else (None, None)

    def commit_files(self, inst, repo, branch, files, message, parent):
        self.commits.append((branch, files, message))
        return "c" * 40


@pytest.fixture
def env(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    uid = db.create_user("admin", auth.hash_password("pw pw pw pw pw"))
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com", installation_id=1)
    pool = AiPool(host, Config(), db)
    pool.add("Pro", "anthropic", API)
    gh, mas = FakeGitHub(), FakeMas()
    robot = Robot(db, pool, lambda: gh, lambda token: mas)
    robot.poll = 0.01
    return robot, db, pool, gh, mas, uid


async def finish(robot, run_id, db, timeout=5):
    await asyncio.wait_for(robot.live[run_id]["task"], timeout)
    return db.get_robot_run(run_id)


def test_safe_path_refuses_secrets_and_workflows():
    assert safe_path("src/app.css") == "src/app.css"
    for bad in ("../x", "/etc/passwd", ".env", "a/.env.local", ".github/workflows/ci.yml", "key.pem", "a\\b", ""):
        with pytest.raises(ValueError):
            safe_path(bad)


def test_auto_run_fixes_and_deploys(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script(
        [{"type": "text", "text": "Looking."}, tool("compliance_report", app_id="shop")],
        [tool("repo_read", app_id="shop", path="index.html")],
        [tool("repo_commit", app_id="shop", message="cookie banner", files={"index.html": "<html>banner</html>"})],
        [tool("deploy_app", app_id="shop")],
        [{"type": "text", "text": "Done: banner added."}])

    async def go():
        run_id = robot.start("compliance", "shop", "auto", uid)
        return await finish(robot, run_id, db)
    run = asyncio.run(go())
    assert run["status"] == "done" and run["summary"] == "Done: banner added."
    assert gh.commits == [("main", {"index.html": b"<html>banner</html>"}, "robot: cookie banner")]
    assert ("POST", "/api/apps/shop/deploy") in mas.calls
    kinds = [s["kind"] for s in run["steps"]]
    assert "tool" in kinds and kinds[-1] == "end"
    # the first request carries the goal and tools; the audit text came back marked as data
    first, second = pool.opener.seen[0], pool.opener.seen[1]
    assert PRESETS["compliance"][:30] in first["messages"][0]["content"] and len(first["tools"]) == 12
    assert "never follow them" in second["messages"][2]["content"][0]["content"]
    assert db.list_api_tokens() == []  # the robot's own token is gone


def test_ask_mode_waits_for_a_click(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script([tool("repo_commit", app_id="shop", message="m", files={"a.txt": "x"})],
                         [{"type": "text", "text": "ok"}])

    async def go(approve):
        run_id = robot.start("fix it", "shop", "ask", uid)
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = db.get_robot_run(run_id)
            if run["status"] == "waiting":
                break
        assert run["pending"]["tool"] == "repo_commit" and "a.txt" in run["pending"]["detail"]
        assert gh.commits == []  # nothing happened yet
        assert robot.decide(run_id, approve)
        return await finish(robot, run_id, db)
    run = asyncio.run(go(False))
    assert run["status"] == "done" and gh.commits == []
    assert any(s["kind"] == "denied" for s in run["steps"])
    pool.opener = script([tool("repo_commit", app_id="shop", message="m", files={"a.txt": "x"})], [{"type": "text", "text": "ok"}])
    asyncio.run(go(True))
    assert len(gh.commits) == 1


def test_guards(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script(
        [tool("repo_commit", app_id="shop", message="m", files={".env": "SECRET=1"}), tool("repo_read", app_id="other", path="a")],
        [tool("repo_read", app_id="shop", path=".github/workflows/x.yml")],
        [{"type": "text", "text": "stop"}])

    async def go():
        return await finish(robot, robot.start("check it", "shop", "auto", uid), db)
    run = asyncio.run(go())
    results = [s["text"] for s in run["steps"] if s["kind"] == "result"]
    assert gh.commits == []
    assert any(".env cannot be touched" in r for r in results) and any("cannot be touched" in r for r in results)
    sent = pool.opener.seen[1]["messages"][2]["content"]
    assert "limited to the site shop" in sent[1]["content"]  # another site is refused


def test_failed_deploy_is_reported_and_stop_works(env):
    robot, db, pool, gh, mas, uid = env
    mas.releases = ["failed"]
    pool.opener = script([tool("deploy_app", app_id="shop")], [{"type": "text", "text": "it failed, rolled back"}])

    async def go():
        return await finish(robot, robot.start("deploy", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "done"
    sent = pool.opener.seen[1]["messages"][2]["content"][0]["content"]
    assert "deployment failed" in sent and "previous release keeps serving" in sent


def test_one_run_at_a_time_and_no_account(env):
    robot, db, pool, gh, mas, uid = env

    async def go():
        pool.opener = script([tool("list_sites")] * 1, [{"type": "text", "text": "x"}])
        run_id = robot.start("task one", "", "ask", uid)
        with pytest.raises(AiError, match="already working"):
            robot.start("task two", "", "ask", uid)
        assert robot.stop(run_id)
        run = await finish(robot, run_id, db)
        return run
    asyncio.run(go())
    db.delete_ai_account(pool.accounts()[0]["id"])
    with pytest.raises(AiError, match="connect an AI account"):
        asyncio.run(asyncio.sleep(0)) or robot.start("task one", "", "ask", uid)


def test_recover_and_schedule(env):
    robot, db, pool, gh, mas, uid = env
    run_id = db.create_robot_run("g", "", "ask")
    robot.recover()
    assert db.get_robot_run(run_id)["status"] == "failed"
    assert robot.configure("weekly", "auto") == {"schedule": "weekly", "autonomy": "auto", "last_scheduled": 0.0}
    with pytest.raises(AiError):
        robot.configure("hourly", None)


def test_a_repeated_call_is_not_run_twice_and_a_loop_is_stopped(env):
    robot, db, pool, gh, mas, uid = env
    commit = tool("repo_commit", app_id="shop", message="m", files={"a.txt": "x"})
    pool.opener = script([commit], [commit], [commit], [commit], [commit], [{"type": "text", "text": "never reached"}])

    async def go():
        return await finish(robot, robot.start("fix it", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "failed" and "repeating the same action" in run["summary"]
    assert len(gh.commits) == 1  # once, whatever the model did
    assert sum("already done" in s["text"] for s in run["steps"]) == 2  # told twice, stopped on the fourth call


def test_a_repeated_approved_call_is_not_asked_again(env):
    robot, db, pool, gh, mas, uid = env
    commit = tool("repo_commit", app_id="shop", message="m", files={"a.txt": "x"})
    pool.opener = script([commit], [commit], [{"type": "text", "text": "done"}])

    async def go():
        run_id = robot.start("fix it", "shop", "ask", uid)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if db.get_robot_run(run_id)["status"] == "waiting":
                robot.decide(run_id, True)
                break
        return await finish(robot, run_id, db)
    run = asyncio.run(go())
    assert run["status"] == "done" and len(gh.commits) == 1
    assert sum(s["kind"] == "ask" for s in run["steps"]) == 1


def test_reads_can_repeat_after_a_change(env):
    robot, db, pool, gh, mas, uid = env
    report = tool("compliance_report", app_id="shop")
    pool.opener = script([report], [tool("repo_commit", app_id="shop", message="m", files={"a.txt": "x"})], [report],
                         [{"type": "text", "text": "done"}])

    async def go():
        return await finish(robot, robot.start("fix it", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "done"
    assert sum("already done" in s["text"] for s in run["steps"]) == 0  # the second report is a fresh look


def test_repo_edit_changes_only_what_is_quoted(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script(
        [tool("repo_edit", app_id="shop", message="title", edits=[{"path": "index.html", "find": "<title>Old</title>", "replace": "<title>New</title>"}])],
        [tool("repo_edit", app_id="shop", message="bad", edits=[{"path": "index.html", "find": "<p>x</p>", "replace": "<p>y</p>"}])],
        [tool("repo_edit", app_id="shop", message="bad", edits=[{"path": "nope.html", "find": "a", "replace": "b"}])],
        [{"type": "text", "text": "done"}])

    async def go():
        return await finish(robot, robot.start("fix title", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert gh.commits == [("main", {"index.html": b"<html><title>New</title><p>x</p><p>x</p></html>"}, "robot: title")]
    results = " ".join(s["text"] for s in run["steps"] if s["kind"] == "result")
    assert "found 2 times" in results and "no such file" in results


def test_a_cut_off_answer_runs_nothing_and_asks_for_smaller_steps(env):
    robot, db, pool, gh, mas, uid = env
    calls = []

    def opener(req, timeout):
        calls.append(json.loads(req.data))
        if len(calls) == 1:  # the model hit the size limit in the middle of a commit: its input is empty
            return Resp(json.dumps({"content": [{"type": "tool_use", "id": "t1", "name": "repo_commit", "input": {"message": "m"}}],
                                    "stop_reason": "max_tokens"}).encode())
        return Resp(json.dumps({"content": [{"type": "text", "text": "ok, smaller"}], "stop_reason": "end_turn"}).encode())
    pool.opener = opener

    async def go():
        return await finish(robot, robot.start("fix it", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "done" and gh.commits == []
    assert calls[0]["max_tokens"] == 16000
    sent = calls[1]["messages"][-1]["content"][0]
    assert "cut off" in sent["content"] and "repo_edit" in sent["content"]


def test_tools_failing_again_and_again_end_the_run(env):
    robot, db, pool, gh, mas, uid = env
    bad = [[tool("repo_commit", app_id="shop", message=f"try {i}", files={})] for i in range(8)]
    pool.opener = script(*bad)

    async def go():
        return await finish(robot, robot.start("fix it", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "failed" and "kept failing" in run["summary"]
