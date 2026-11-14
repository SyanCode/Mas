"""Autopilot of the advisor: proposals applied by the robot on their own, and closed."""

from __future__ import annotations

import time

import pytest

from mas.ai import AiError
from mas.autopilot import MAX_ATTEMPTS, Autopilot
from mas.db import Database


class FakeRobot:
    def __init__(self, db):
        self.db, self.busy, self.started = db, False, []

    def active(self):
        return 1 if self.busy else None

    def start(self, goal, app_id, autonomy, user_id):
        if self.busy:
            raise AiError("the robot is already working")
        run_id = self.db.create_robot_run(goal, app_id, autonomy)
        self.started.append((run_id, goal, app_id, autonomy))
        return run_id


class FakePool:
    ok = True
    limited = False  # every account at its usage limit

    def configured(self):
        return self.ok

    def usable(self):
        return [] if self.limited or not self.ok else [object()]


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


def test_off_by_default_and_never_touches_the_server_proposals(ap):
    a, db, robot, pool = ap
    propose(db, "Add a cookie banner", "Shop")
    assert a.tick(1) == [] and robot.started == []  # off
    a.configure(enabled=True)
    propose(db, "Free some memory", "")  # about the server: nothing the robot can do
    propose(db, "Fix the blog", "blog")  # an app the robot cannot reach (no GitHub App)
    propose(db, "Mystery", "nowhere")
    assert a.tick(1) == ["started:1"]
    assert [r[2:] for r in robot.started] == [("shop", "auto")]  # the robot works in auto mode
    assert "Add a cookie banner" in robot.started[0][1]
    robot.busy = True
    assert a.tick(1) == [] and len(robot.started) == 1  # one at a time


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
    assert a.run_all() == {"queued": 4, "manual": 1}
    order = []
    for _ in range(12):
        a.tick(1)
        if len(robot.started) > len(order):
            order.append(robot.started[-1][1].split(".")[0])
            finish(db, robot.started[-1][0], "done", "ok")
    assert order[0] == "Thing 1" and len(order) == 4  # the critical one first, no daily cap
    assert len(db.list_suggestions("done")) == 4
    st = a.settings()
    assert st["batch"] is False and st["left"] == 0 and st["manual"] == 1  # finished: it stops, the server one stays
    assert db.list_suggestions("new")[0]["title"] == "Free some memory"
    a.tick(1)
    assert len(robot.started) == 4


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
