"""Autopilot of the advisor: proposals applied by the robot on their own, and closed."""

from __future__ import annotations

import time

import pytest

from mas.ai import AiError
from mas.autopilot import MAX_ATTEMPTS, Autopilot
from mas.db import Database


class _Task:
    def done(self):
        return False


class FakeRobot:
    def __init__(self, db):
        self.db, self.busy, self.started, self.live, self.stopped = db, False, [], {}, []

    def stop(self, run_id):
        self.stopped.append(run_id)
        return True

    def active(self):
        return 1 if self.busy else None

    def start(self, goal, app_id, autonomy, user_id):
        if self.busy:
            raise AiError("the robot is already working")
        run_id = self.db.create_robot_run(goal, app_id, autonomy)
        self.started.append((run_id, goal, app_id, autonomy))
        self.live[run_id] = {"task": _Task()}
        return run_id


class FakePool:
    ok = True
    limited = False  # every account at its usage limit

    def configured(self):
        return self.ok

    def usable(self):
        return [] if self.limited or not self.ok else [object()]

    accounts_list = [{"name": "Pro", "enabled": True, "error": ""}]

    def accounts(self):
        return self.accounts_list if self.ok else []


@pytest.fixture
def ap(tmp_path):
    db = Database(tmp_path / "mas.db")
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com",
                  installation_id=1)
    db.create_app("blog", name="Blog", repo="acme/blog", branch="main", type="static", domain="blog.example.com")  # no GitHub App
    robot = FakeRobot(db)
    pool = FakePool()
    return Autopilot(db, robot, pool), db, robot, pool


def propose(db, title, target, severity="warning"):
    assert db.add_suggestion(title.lower(), severity, title, f"detail of {title}", target)
    return next(s["id"] for s in db.list_suggestions() if s["title"] == title)


def finish(db, run_id, status, summary=""):
    db.update_robot_run(run_id, status=status, summary=summary, finished_at=time.time())


def test_off_by_default_and_the_server_proposals_run_in_server_mode(ap):
    a, db, robot, pool = ap
    propose(db, "Add a cookie banner", "Shop")
    assert a.tick(1) == [] and robot.started == []  # off
    a.configure(enabled=True)
    assert a.tick(1) == ["started:1"]
    assert [r[2:] for r in robot.started] == [("shop", "auto")]  # a site: a run on it, in auto mode
    assert "Add a cookie banner" in robot.started[0][1] and "Server proposal" not in robot.started[0][1]
    robot.busy = True
    assert a.tick(1) == [] and len(robot.started) == 1  # one at a time
    robot.busy = False
    finish(db, robot.started[0][0], "done", "ok\nRESULT: done")
    propose(db, "Free some memory", "")  # about the server itself
    propose(db, "Fix the blog", "blog")  # a site the robot cannot reach (no GitHub App): the server tools
    a.tick(1)
    assert robot.started[1][2] == "" and "Server proposal" in robot.started[1][1] and "Free some memory" in robot.started[1][1]
    finish(db, robot.started[1][0], "done", "ok")
    a.tick(1)
    assert robot.started[2][2] == "" and "(about: blog)" in robot.started[2][1]


def test_a_finished_run_closes_the_proposal_with_its_report(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    sid = propose(db, "Add a cookie banner", "shop")
    a.tick(1)
    assert db.list_suggestions("working")[0]["id"] == sid
    run_id = robot.started[0][0]
    assert a.tick(1) == []  # still running
    finish(db, run_id, "done", "Banner added and deployed.")
    assert a.tick(1) == [f"done:{sid}"]
    done = db.list_suggestions("done")[0]
    assert done["result"] == "Banner added and deployed." and done["finished_at"] and done["run_id"] == run_id
    assert a.settings()["applied"] == 1
    assert any(e["action"] == "autopilot-done" for e in db.history(None, 20))


def test_most_severe_first_one_retry_after_an_hour_then_failed(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True, max_per_day=10)
    tip = propose(db, "A tip", "shop", "info")
    crit = propose(db, "Critical thing", "shop", "critical")
    a.tick(1)
    assert robot.started[0][2] == "shop" and "Critical thing" in robot.started[0][1]
    finish(db, robot.started[0][0], "failed", "build broke")
    a.tick(1)  # settles the failure, then starts the tip
    first = next(s for s in db.list_suggestions() if s["id"] == crit)
    assert first["status"] == "new" and first["attempts"] == 1 and first["result"] == "build broke"
    assert "A tip" in robot.started[1][1]
    finish(db, robot.started[1][0], "done", "ok")
    a.tick(1)
    assert len(robot.started) == 2  # the failed one waits an hour
    db.update_suggestion(crit, finished_at=time.time() - 3700)
    a.tick(1)
    assert "Critical thing" in robot.started[2][1]
    finish(db, robot.started[2][0], "failed", "again")
    a.tick(1)
    last = next(s for s in db.list_suggestions() if s["id"] == crit)
    assert last["status"] == "failed" and last["attempts"] == MAX_ATTEMPTS  # a human decides now
    assert tip and len(robot.started) == 3


def test_daily_limit(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True, max_per_day=2)
    for n in range(4):
        propose(db, f"Thing {n}", "shop")
    for _ in range(4):
        a.tick(1)
        if robot.started:
            finish(db, robot.started[-1][0], "done", "ok")
    a.tick(1)
    assert len(robot.started) == 2 and a.settings()["started_today"] == 2  # two a day, the others wait for tomorrow
    a.configure(max_per_day=3)
    a.tick(1)
    assert len(robot.started) == 3


def test_three_failures_in_a_row_pause_it_until_it_is_turned_on_again(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True, max_per_day=10)
    for n in range(5):
        propose(db, f"Thing {n}", "shop")
    for n in range(3):
        a.tick(1)
        assert len(robot.started) == n + 1
        finish(db, robot.started[-1][0], "failed", "boom")
    a.tick(1)  # settles the third failure
    assert "3 runs failed in a row" in a.settings()["paused"]
    a.tick(1)
    assert len(robot.started) == 3  # paused: nothing new starts
    a.configure(enabled=True)
    assert a.settings()["paused"] == ""
    a.tick(1)
    assert len(robot.started) == 4


def test_no_ai_account_no_run_and_settings_are_validated(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    propose(db, "Something", "shop")
    pool.ok = False
    assert a.tick(1) == [] and robot.started == []
    assert a.tick(None) == []
    for bad in (-1, 51, True, "3"):
        with pytest.raises(AiError):
            a.configure(max_per_day=bad)


def test_api_settings_and_nobody_but_administrators(tmp_path):
    from tests.test_notify import make
    client, db, n, hooks, h = make(tmp_path)
    got = client.get("/api/advisor").json()["autopilot"]
    assert got["enabled"] is False and got["max_per_day"] == 0 and got["paused"] == ""  # no cap: the AI's limit is the limit
    r = client.put("/api/advisor/autopilot", headers=h, json={"enabled": True, "max_per_day": 5})
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["max_per_day"] == 5
    assert client.put("/api/advisor/autopilot", headers=h, json={"max_per_day": 51}).status_code == 422
    assert client.get("/api/advisor").json()["autopilot"]["enabled"] is True
    assert any(e["action"] == "autopilot-settings" for e in db.history(None, 10))
    # API tokens and read-only accounts never switch it on
    from mas import auth
    assert not auth.api_token_allowed("/api/advisor/autopilot")


def test_back_to_review_gives_a_failed_proposal_a_fresh_start(tmp_path):
    from tests.test_notify import make
    client, db, n, hooks, h = make(tmp_path)
    sid = propose(db, "Add a banner", "shop")
    db.update_suggestion(sid, status="failed", attempts=2, result="boom", finished_at=time.time())
    assert client.get("/api/advisor").json()["new"] == 1  # a failed one still waits for somebody
    assert client.put(f"/api/advisor/suggestions/{sid}", headers=h, json={"status": "new"}).status_code == 200
    s = db.list_suggestions("new")[0]
    assert s["attempts"] == 0 and s["result"] is None and s["finished_at"] is None


def test_apply_everything_in_a_row_then_it_stops(ap):
    a, db, robot, pool = ap
    for n in range(4):
        propose(db, f"Thing {n}", "shop", ["info", "critical", "warning", "info"][n])
    propose(db, "Free some memory", "")  # about the server: left to a person
    assert a.tick(1) == []  # not asked: nothing starts by itself
    assert a.run_all() == {"queued": 5}
    order = []
    for _ in range(15):
        a.tick(1)
        if len(robot.started) > len(order):
            order.append(robot.started[-1][1].split(".")[0])
            finish(db, robot.started[-1][0], "done", "ok")
    assert order[0] == "Thing 1" and len(order) == 5  # the critical one first, no daily cap
    assert len(db.list_suggestions("done")) == 5  # the server one too
    st = a.settings()
    assert st["batch"] is False and st["left"] == 0  # finished: it stops by itself
    a.tick(1)
    assert len(robot.started) == 5


def test_the_ai_limit_pauses_the_queue_without_counting_as_a_failure(ap):
    a, db, robot, pool = ap
    propose(db, "One", "shop")
    propose(db, "Two", "shop")
    a.run_all()
    pool.limited = True
    assert a.tick(1) == [] and robot.started == []  # at its limit: nothing starts
    pool.limited = False
    a.tick(1)
    assert len(robot.started) == 1
    pool.limited = True  # the run is cut short by the usage limit
    finish(db, robot.started[0][0], "failed", "no AI account could answer (Pro: limit reached)")
    assert a.tick(1)[0].startswith("limit:")
    back = next(s for s in db.list_suggestions() if s["title"] == "One")
    assert back["status"] == "new" and back["attempts"] == 0  # not a failed attempt
    assert int(db.get_setting("autopilot_failures") or 0) == 0 and a.settings()["paused"] == ""
    assert a.tick(1) == [] and len(robot.started) == 1
    pool.limited = False  # the limit resets: it carries on by itself
    a.tick(1)
    assert len(robot.started) == 2


def test_the_standing_option_applies_new_proposals_as_they_come(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    propose(db, "First", "shop")
    a.tick(1)
    finish(db, robot.started[0][0], "done", "ok")
    a.tick(1)
    propose(db, "Second", "shop")  # the advisor found something new later
    a.tick(1)
    assert len(robot.started) == 2 and "Second" in robot.started[1][1]


def test_apply_all_button_analyses_when_nothing_waits(tmp_path):
    from tests.test_notify import make
    client, db, n, hooks, h = make(tmp_path)
    ran = []
    advisor = client.app.state.advisor
    advisor.run = lambda force=True: (ran.append(1), db.add_suggestion("fp", "info", "Add alt texts", "d", "shop"), {"added": 1})[2]
    r = client.post("/api/advisor/apply-all", headers=h)
    assert r.status_code == 200 and r.json()["added"] == 1 and r.json()["queued"] == 1 and ran == [1]
    assert r.json()["autopilot"]["batch"] is True
    again = client.post("/api/advisor/apply-all", headers=h).json()
    assert ran == [1] and again["queued"] == 1  # something waits: no new analysis
    from mas import auth
    assert not auth.api_token_allowed("/api/advisor/apply-all")


def test_the_robot_says_when_it_needs_a_person(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    sid = propose(db, "Move the database to another disk", "")
    a.tick(1)
    finish(db, robot.started[0][0], "done", "I have no tool to move data.\nA person must stop the database, copy it and remount.\nRESULT: needs a person")
    assert a.tick(1) == [f"needs:{sid}"]
    s = db.list_suggestions()[0]
    assert s["status"] == "failed" and s["attempts"] == 2 and "A person must stop" in s["result"] and "RESULT" not in s["result"]
    assert int(db.get_setting("autopilot_failures") or 0) == 0  # not a failure of the robot: it does not count towards the pause
    a.tick(1)
    assert len(robot.started) == 1  # and it is not tried again
    assert Autopilot.verdict("all good\nRESULT: done") == ("done", "all good")
    assert Autopilot.verdict("no marker at all") == (None, "no marker at all")


def test_a_stuck_run_is_closed_and_stop_leaves_the_queue(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    sid = propose(db, "Something", "shop")
    a.tick(1)
    run_id = robot.started[0][0]
    assert a.settings()["current"]["title"] == "Something"
    robot.live.clear()  # the database says running, nobody is running it
    assert a.tick(1)[0] == f"failed:{sid}"
    assert db.get_robot_run(run_id)["status"] == "failed"
    # Stop: the run is stopped, the queue and the standing option are off, and a stopped run is not a failure
    db.update_suggestion(sid, finished_at=0)
    a.run_all()
    a.tick(1)
    run2 = robot.started[-1][0]
    assert a.stop() is True and robot.stopped == [run_id, run2]  # the stuck one was stopped too
    st = a.settings()
    assert st["batch"] is False and st["enabled"] is False
    finish(db, run2, "stopped", "stopped")
    before = int(db.get_setting("autopilot_failures") or 0)
    a.tick(1)
    s = next(x for x in db.list_suggestions() if x["id"] == sid)
    assert s["status"] == "new" and int(db.get_setting("autopilot_failures") or 0) == before


def test_it_says_why_nothing_starts(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    propose(db, "Something", "shop")
    robot.busy = True  # an old run still holds the robot
    st = a.settings()
    assert st["blocked"]["reason"] == "robot_busy"
    robot.busy = False
    pool.limited = True
    assert a.settings()["blocked"] is None  # the usage limit has its own message (limited_until)
    pool.limited = False
    assert a.settings()["blocked"] is None
    a.tick(1)
    assert a.settings()["blocked"] is None  # it started
    a.configure(max_per_day=1)
    finish(db, robot.started[0][0], "done", "ok")
    a.tick(1)
    propose(db, "Another", "shop")
    assert a.settings()["blocked"]["reason"] == "cap"


def test_an_error_in_the_loop_is_shown_not_swallowed(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    propose(db, "Something", "shop")
    robot.start = lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
    assert a.tick(1) == []
    assert "RuntimeError: boom" in a.settings()["error"]
    robot.start = lambda goal, app_id, autonomy, user_id: 1
    a.tick(1)
    assert a.settings()["error"] == ""


def test_every_account_switched_off_is_explained(ap):
    a, db, robot, pool = ap
    a.configure(enabled=True)
    propose(db, "Something", "shop")
    pool.accounts_list = [{"name": "Pro", "enabled": False, "error": "refused (401): the key was revoked"}]
    pool.limited = True  # nothing usable
    blocked = a.settings()["blocked"]
    assert blocked["reason"] == "ai_disabled" and "key was revoked" in blocked["detail"]
    pool.ok = False
    assert a.settings()["blocked"]["reason"] == "no_account"
