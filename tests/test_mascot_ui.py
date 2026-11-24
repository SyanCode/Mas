"""The robot mascot in a real browser: the built dashboard served by Mas, an AI account answering from a script.

Skipped unless the dashboard is built (``cd web && npm run build``) and Playwright + Chromium are there.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import server
import uvicorn

from mas import auth, browser_audit
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

OUT = Path(__file__).resolve().parent.parent / "web" / "out"
pytestmark = pytest.mark.skipif(not (OUT / "index.html").is_file() or browser_audit.find_chromium() is None,
                                reason="needs the built dashboard and Chromium")
PASSWORD = "correct horse battery"
API = "sk-ant-api03-" + "b" * 40


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Claude:
    """Answers like the Anthropic API: a question gets a plain answer, a task asks to deploy (a gated tool)."""

    def __init__(self) -> None:
        self.delay = 0.6

    def __call__(self, req, timeout):
        body = json.loads(req.data)
        time.sleep(self.delay)
        first = body["messages"][0]["content"]
        text = first if isinstance(first, str) else first[0]["text"]
        system = " ".join(b["text"] for b in body["system"])
        if "You prepare work for the robot" in system:  # auto-prompting: one question, then the goal
            asked = json.loads(text)
            refined = ({"ready": True, "questions": [], "why": "Clear now.",
                        "goal": f"Deploy the site {asked['answers'][0]['answer']} and check it answers."}
                       if asked["answers"] else
                       {"ready": False, "goal": "Deploy a site.", "why": "",
                        "questions": [{"question": "Which site should be deployed?", "options": ["site", "every site"]}]})
            blocks = [{"type": "text", "text": json.dumps(refined)}]
        elif "This is a QUESTION" in text:
            blocks = [{"type": "text", "text": "Everything is running: 1 site, no problem found.\nRESULT: done"}]
        elif len(body["messages"]) == 1:
            blocks = [{"type": "tool_use", "id": "t1", "name": "deploy_app", "input": {"app_id": "site"}}]
        else:
            blocks = [{"type": "text", "text": "You refused the deployment, nothing changed.\nRESULT: needs a person"}]
        stop = "tool_use" if blocks[0]["type"] == "tool_use" else "end_turn"
        return Resp(json.dumps({"content": blocks, "stop_reason": stop,
                                "usage": {"input_tokens": 100, "output_tokens": 20}}).encode())


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from playwright.sync_api import sync_playwright
    tmp = tmp_path_factory.mktemp("mascot")
    host = FixtureHost(server.build(tmp / "srv"))
    db = Database(tmp / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    db.create_app("site", name="Client site", repo="acme/site", branch="main", installation_id=7, type="static",
                  domain="client.example.com", auto_deploy=0)
    app = create_app(host, Config(cookie_secure=False), db, background=False, web_dir=OUT)
    app.state.pool.add("Pro", "anthropic", API)
    app.state.pool.opener = Claude()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server_ = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server_.run, daemon=True).start()
    for _ in range(100):
        if server_.started:
            break
        time.sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_audit.find_chromium(), args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
        ctx.add_init_script("localStorage.setItem('mas-lang', 'en')")
        assert ctx.request.post(f"http://127.0.0.1:{port}/api/login", data={"username": "admin", "password": PASSWORD}).ok
        yield ctx, f"http://127.0.0.1:{port}", db
        browser.close()
    server_.should_exit = True


def open_page(live, path="/"):
    ctx, base, _ = live
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path)
    return page, errors


def test_ask_a_question_read_only(live):
    _, _, db = live
    page, errors = open_page(live)
    mascot = page.get_by_role("button", name="Ask the robot")
    mascot.wait_for()
    box = mascot.bounding_box()
    assert box["x"] > 1400 - 120  # docked on the right by default
    mascot.click()
    dialog = page.get_by_role("dialog", name="Robot")
    dialog.wait_for()
    box = page.get_by_placeholder("Ask the robot…")
    box.fill("Is everything running on my server?")
    box.press("Enter")
    dialog.get_by_text("Is everything running on my server?").wait_for()
    dialog.get_by_text("Everything is running: 1 site, no problem found.").wait_for(timeout=15000)
    assert "RESULT:" not in dialog.inner_text()
    if os.environ.get("MAS_SCREENSHOTS"):  # a picture for the README / a review
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(os.environ["MAS_SCREENSHOTS"], "mascot-panel.png"))
    run = db.list_robot_runs(1)[0]
    assert run["autonomy"] == "read" and run["status"] == "done"
    assert not errors, errors


def test_drag_to_the_other_side_hide_and_show(live):
    page, errors = open_page(live)
    mascot = page.get_by_role("button", name="Ask the robot")
    mascot.wait_for()
    box = mascot.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(400, 300, steps=8)
    page.mouse.move(60, 250, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    box = page.get_by_role("button", name="Ask the robot").bounding_box()
    assert box["x"] < 120 and 150 < box["y"] < 350, box
    assert page.get_by_role("dialog", name="Robot").count() == 0  # a drag is not a click
    place = json.loads(page.evaluate("localStorage.getItem('mas-mascot')"))
    assert place["side"] == "left" and not place["hidden"]
    page.reload()
    assert page.get_by_role("button", name="Ask the robot").bounding_box()["x"] < 120  # remembered

    page.get_by_role("button", name="Ask the robot").click()
    page.get_by_role("button", name="Hide the robot").click()
    page.get_by_role("button", name="Show the robot").click()
    page.get_by_role("button", name="Ask the robot").wait_for()
    assert not errors, errors


def test_a_task_waits_for_a_click_and_a_question_runs_beside_it(live):
    ctx, base, db = live
    db.set_setting("robot_autonomy", "ask")
    page, errors = open_page(live)
    page.get_by_role("button", name="Ask the robot").click()
    dialog = page.get_by_role("dialog", name="Robot")
    if dialog.get_by_role("button", name="New question").count():
        dialog.get_by_role("button", name="New question").click()
    page.get_by_placeholder("Ask the robot…").fill("Deploy the client site now please")
    dialog.get_by_role("button", name="Do it").click()
    # auto-prompting: Claude asks which site, then shows the goal it wrote, which can be edited
    dialog.get_by_text("Which site should be deployed?").wait_for(timeout=15000)
    dialog.get_by_role("button", name="site", exact=True).click()
    if os.environ.get("MAS_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["MAS_SCREENSHOTS"], "mascot-coach.png"))
    dialog.get_by_role("button", name="Continue").click()
    goal = dialog.get_by_label("Goal for the robot")
    goal.wait_for(timeout=15000)
    assert goal.input_value() == "Deploy the site site and check it answers."
    goal.fill("Deploy the site site and check it answers. Do not touch anything else.")
    dialog.get_by_role("button", name="Start with this goal").click()
    dialog.get_by_text("The robot asks your permission").wait_for(timeout=15000)
    assert db.list_robot_runs(1)[0]["goal"] == "Deploy the site site and check it answers. Do not touch anything else."
    # the panel closed, the robot calls for attention
    page.get_by_role("button", name="Close", exact=True).click()
    page.get_by_text("I need your go-ahead").wait_for(timeout=15000)
    # a question meanwhile: allowed, it changes nothing; then the task waits again for its answer
    page.get_by_role("button", name="Ask the robot").click()
    dialog.get_by_text("The robot asks your permission").wait_for()
    page.get_by_placeholder("Ask the robot…").fill("Is the client site up right now?")
    dialog.get_by_role("button", name="Ask", exact=True).click()
    dialog.get_by_text("Everything is running: 1 site, no problem found.").wait_for(timeout=15000)
    dialog.get_by_role("button", name="Refuse").click()  # the task's box stays on top of the answer
    deadline = time.time() + 15
    while time.time() < deadline and next(r for r in db.list_robot_runs(5) if r["autonomy"] == "ask")["status"] != "done":
        time.sleep(0.2)
    dialog.get_by_text("Everything is running: 1 site, no problem found.").wait_for()
    assert dialog.get_by_role("button", name="Refuse").count() == 0
    task = next(r for r in db.list_robot_runs(5) if r["autonomy"] == "ask")
    assert task["status"] == "done"
    assert not any(h["action"] == "robot-deploy" for h in db.history(limit=30))
    assert not errors, errors
