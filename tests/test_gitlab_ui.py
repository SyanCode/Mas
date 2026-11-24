"""Settings → GitLab in a real browser: a GitLab of this server is found, used and connected.

Skipped unless the dashboard is built (``cd web && npm run build``) and Playwright + Chromium are there.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import server
import uvicorn

from mas import auth, browser_audit, gitlab
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

OUT = Path(__file__).resolve().parent.parent / "web" / "out"
pytestmark = pytest.mark.skipif(not (OUT / "index.html").is_file() or browser_audit.find_chromium() is None,
                                reason="needs the built dashboard and Chromium")
PASSWORD = "correct horse battery"


def test_a_local_gitlab_is_found_and_connected(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    host = FixtureHost(server.build(tmp_path / "srv"))
    host.write_text("/etc/gitlab/gitlab.rb", "external_url 'https://git.example.com'\n")
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False, web_dir=OUT)
    seen = []
    monkeypatch.setattr(gitlab.GitLab, "whoami", lambda self: seen.append(self.url) or {"username": "root", "name": "Admin", "version": "17.5.1"})
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
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=browser_audit.find_chromium(), args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
            ctx.add_init_script("localStorage.setItem('mas-lang', 'en'); localStorage.setItem('mas-mascot', JSON.stringify({side:'right',y:0.8,hidden:true}))")
            base = f"http://127.0.0.1:{port}"
            assert ctx.request.post(f"{base}/api/login", data={"username": "admin", "password": PASSWORD}).ok
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/settings/")
            page.get_by_text("Found on your servers").wait_for()
            page.get_by_text("https://git.example.com").first.wait_for()
            page.get_by_role("button", name="Use it").click()
            assert page.get_by_placeholder("https://gitlab.example.com").input_value() == "https://git.example.com"
            page.get_by_placeholder("glpat-…").fill("glpat-" + "y" * 20)
            page.get_by_label("Your Mas password").fill(PASSWORD)
            page.get_by_role("button", name="Connect GitLab").click()
            page.get_by_text("Connected to https://git.example.com as root.").wait_for()
            if os.environ.get("MAS_SCREENSHOTS"):
                page.get_by_text("Found on your servers").scroll_into_view_if_needed()
                page.screenshot(path=os.path.join(os.environ["MAS_SCREENSHOTS"], "gitlab-settings.png"))
            assert seen[-1] == "https://git.example.com" and db.get_setting(gitlab.S_URL) == "https://git.example.com"
            assert not errors, errors
            browser.close()
    finally:
        server_.should_exit = True
