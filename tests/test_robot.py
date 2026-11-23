"""The robot: tool loop, approvals, guarded paths, deployment, short-lived token."""

from __future__ import annotations

import asyncio
import io
import json
import time

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
        if path == "/api/health/checks":
            return {"checks": [{"level": "error", "category": "certificates", "title": "Certificate expires in 3 days",
                                "detail": "shop.example.com", "fix": "certbot renew", "action": "certbot-renew", "target": "shop.example.com"}]}
        if path == "/api/services":
            return {"services": [{"id": "api.example.com", "name": "api", "type": "node", "manager": "systemd", "status": "stopped",
                                  "domains": ["api.example.com"]}]}
        if path.endswith("/compliance"):
            return {"latest": {"score": 40, "verdict": "blocking", "counts": {}, "findings": [
                {"level": "critical", "title": "No cookie banner", "detail": "d", "fix": "add one"}]}}
        raise AssertionError(path)

    async def post(self, path, **kw):
        self.calls.append(("POST", path))
        if path == "/api/health/fix":
            return {"ok": True, "output": "renewed"}
        if path.startswith("/api/services/") and path.endswith(("/restart", "/reload")):
            return {"ok": True, "output": ""}
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
    assert PRESETS["compliance"][:30] in first["messages"][0]["content"][0]["text"] and len(first["tools"]) == 19
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


def test_a_server_run_reads_the_checks_fixes_and_restarts(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script(
        [tool("health_checks"), tool("server_status")],
        [tool("apply_health_fix", action="certbot-renew", target="shop.example.com")],
        [tool("restart_service", service_id="api.example.com", action="restart")],
        [{"type": "text", "text": "Renewed the certificate and restarted the API.\nRESULT: done"}])

    async def go():
        return await finish(robot, robot.start("Certificate expires soon. Renew it.", "", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "done" and run["summary"].endswith("RESULT: done")
    assert ("POST", "/api/health/fix") in mas.calls and ("POST", "/api/services/api.example.com/actions/restart") in mas.calls
    first = pool.opener.seen[0]
    assert {"health_checks", "server_status", "apply_health_fix", "restart_service"} <= {t["name"] for t in first["tools"]}
    assert any(e["action"] == "robot-health-fix" for e in db.history(None, 20))


def test_a_run_on_one_site_neither_sees_nor_runs_the_server_tools(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script([tool("restart_service", service_id="api.example.com")], [{"type": "text", "text": "ok"}])

    async def go():
        return await finish(robot, robot.start("Improve the style of the site please", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert not any(t["name"] in ("health_checks", "restart_service", "apply_health_fix", "server_status")
                   for t in pool.opener.seen[0]["tools"])
    assert ("POST", "/api/services/api.example.com/actions/restart") not in mas.calls
    assert "not available in a run limited" in json.dumps(pool.opener.seen[1]["messages"])


def test_only_restart_and_reload_exist(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script([tool("restart_service", service_id="api.example.com", action="stop")], [{"type": "text", "text": "ok"}])

    async def go():
        return await finish(robot, robot.start("The API is down, bring it back", "", "auto", uid), db)
    asyncio.run(go())
    assert not any(c[1].endswith("/stop") for c in mas.calls)


def test_reap_frees_a_robot_stuck_on_a_hung_run(env):
    robot, db, pool, gh, mas, uid = env

    async def hang():
        await asyncio.sleep(3600)

    async def go():
        run_id = db.create_robot_run("a goal that hangs for ever", "", "auto")
        robot.live[run_id] = {"task": asyncio.create_task(hang()), "event": asyncio.Event(), "approve": False, "stop": False, "seen": {}}
        assert robot.active() == run_id
        assert robot.reap() == []  # still within its time
        assert robot.reap(now=time.time() + 3 * 3600) == [run_id]  # far too long
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert db.get_robot_run(run_id)["status"] == "failed" and "did not finish in time" in db.get_robot_run(run_id)["summary"]
        assert robot.active() is None
        # a run the database already calls over, but whose task lingers, is cancelled too
        run2 = db.create_robot_run("x" * 10, "", "auto")
        db.update_robot_run(run2, status="done", finished_at=1.0)
        robot.live[run2] = {"task": asyncio.create_task(hang()), "event": asyncio.Event(), "approve": False, "stop": False, "seen": {}}
        assert robot.reap() == [run2]
    asyncio.run(go())


def test_skills_are_loaded_on_demand_and_polish_reads_the_repository(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script(
        [{**tool("load_skill"), "input": {"name": "not-vibecoded"}}],
        [tool("polish_report", app_id="shop")],
        [tool("install_skills", app_id="shop")],
        [{"type": "text", "text": "Report.\nRESULT: needs a person"}])

    async def go():
        run_id = robot.start("polish", "shop", "auto", uid)
        return await finish(robot, run_id, db)
    run = asyncio.run(go())
    assert run["status"] == "done", run
    first = pool.opener.seen[0]
    # the prompt lists the skills (names and descriptions only), the goal asks for the anti-vibecode skills
    system = " ".join(b["text"] for b in first["system"])
    assert "- seo-checklist:" in system and "Unlock / elevate" not in system
    assert "not-vibecoded" in first["messages"][0]["content"][0]["text"]
    loaded = pool.opener.seen[1]["messages"][2]["content"][0]["content"]
    assert "Design tells" in loaded and "follow them" in loaded
    polished = json.loads(pool.opener.seen[2]["messages"][4]["content"][0]["content"].split("\n", 1)[1])
    ids = {f["id"] for f in polished["findings"]}
    assert {"favicon-missing", "404-missing", "readme-missing"} <= ids and polished["files_read"] == 1
    # the skills are committed as Claude Code skills, never under a blocked path
    (branch, files, message), = gh.commits
    assert "robot:" in message and ".claude/skills/seo-checklist/SKILL.md" in files
    assert files[".claude/skills/not-vibecoded/SKILL.md"].startswith(b"---\nname: not-vibecoded\n")


def test_unknown_skill_is_an_error_not_a_crash(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script([{**tool("load_skill"), "input": {"name": "../../etc/passwd"}}], [{"type": "text", "text": "ok"}])

    async def go():
        return await finish(robot, robot.start("seo", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["status"] == "done"
    assert pool.opener.seen[1]["messages"][2]["content"][0]["content"].startswith("error: unknown skill")


def test_a_run_records_its_tokens_and_cache_use(env):
    robot, db, pool, gh, mas, uid = env
    turns = [[tool("list_sites")], [{"type": "text", "text": "ok"}]]
    usages = [{"input_tokens": 3000, "cache_creation_input_tokens": 5000, "output_tokens": 100},
              {"input_tokens": 200, "cache_read_input_tokens": 8000, "output_tokens": 50}]

    def opener(req, timeout):
        blocks = turns.pop(0)
        stop = "tool_use" if blocks[0]["type"] == "tool_use" else "end_turn"
        return Resp(json.dumps({"content": blocks, "stop_reason": stop, "usage": usages.pop(0)}).encode())
    pool.opener = opener

    async def go():
        return await finish(robot, robot.start("seo", "shop", "auto", uid), db)
    run = asyncio.run(go())
    assert run["usage"] == {"input": 3200, "cache_write": 5000, "cache_read": 8000, "output": 150, "calls": 2}
    assert db.list_robot_runs(1)[0]["usage"]["calls"] == 2
    month = db.robot_usage(time.time() - 30 * 86400)
    assert month["cache_read"] == 8000 and month["runs"] == 1 and db.robot_usage(time.time() + 10)["runs"] == 0


def test_a_question_cannot_change_anything(env):
    robot, db, pool, gh, mas, uid = env
    pool.opener = script([tool("deploy_app", app_id="shop")], [{"type": "text", "text": "The site is fine."}])

    async def go():
        return await finish(robot, robot.start("Is my shop site healthy?", "shop", "read", uid), db)
    run = asyncio.run(go())
    first = pool.opener.seen[0]
    names = {t["name"] for t in first["tools"]}
    assert "deploy_app" not in names and "repo_edit" not in names and "seo_report" in names
    assert "This is a QUESTION" in first["messages"][0]["content"][0]["text"]
    assert "only answers a question" in pool.opener.seen[1]["messages"][2]["content"][0]["content"]
    assert run["status"] == "done" and run["summary"] == "The site is fine." and ("POST", "/api/apps/shop/deploy") not in mas.calls
    with pytest.raises(AiError):
        robot.configure(None, "read")  # never the autonomy of scheduled rounds
