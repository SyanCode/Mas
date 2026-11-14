"""The CMS back-office in a real browser: the built dashboard served by Mas, GitHub in memory.

Skipped unless the dashboard is built (``cd web && npm run build``) and Playwright + Chromium are there.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import server
import uvicorn
from test_cms import PNG, SCHEMA
from test_revert import HistoryGitHub

from mas import auth, browser_audit
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

OUT = Path(__file__).resolve().parent.parent / "web" / "out"
pytestmark = pytest.mark.skipif(not (OUT / "cms" / "index.html").is_file() or not browser_audit.__dict__.get("find_chromium")
                                or browser_audit.find_chromium() is None, reason="needs the built dashboard and Chromium")
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from playwright.sync_api import sync_playwright
    tmp = tmp_path_factory.mktemp("ui")
    host = FixtureHost(server.build(tmp / "srv"))
    db = Database(tmp / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    db.create_app("site", name="Client site", repo="acme/site", branch="main", installation_id=7, type="static",
                  domain="client.example.com", auto_deploy=0)
    app = create_app(host, Config(cookie_secure=False), db, background=False, web_dir=OUT)
    schema = json.loads(json.dumps(SCHEMA))
    schema["collections"][0]["drafts"] = True
    gh = HistoryGitHub({"mas.cms.json": json.dumps(schema).encode(), "content/home.json": b'{"title": "Hello"}\n',
                     "content/articles/hand.json": b'{"title": "By hand", "date": "2025-12-01", "status": "published"}',
                     "public/uploads/old.png": PNG})
    app.state.cms.github = lambda: gh
    app.state.cms.deployer.enqueue = lambda app_id, trigger, actor, sha=None: 42
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
        yield ctx, f"http://127.0.0.1:{port}", db, gh
        browser.close()
    server_.should_exit = True


def open_page(live, path):
    ctx, base, *_ = live
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path)
    return page, errors


def test_dashboard_sidebar_and_command_palette(live):
    page, errors = open_page(live, "/cms/?app=site")
    page.get_by_role("heading", name="Dashboard").wait_for()
    page.get_by_role("link", name="Articles").first.wait_for()  # the sidebar fills once the site overview is loaded
    for name in ("Articles", "Media", "Statistics", "Availability", "Compliance", "Legal pages", "Cookie banner", "History", "Users"):
        assert page.get_by_role("link", name=name).first.is_visible(), name
    page.keyboard.press("Control+k")
    page.get_by_placeholder("Search pages, entries, actions…").fill("by hand")
    page.get_by_role("option", name="By hand").wait_for()
    page.keyboard.press("Enter")
    page.wait_for_url("**/cms/entry/**slug=hand")
    page.get_by_label("Title *").wait_for()
    assert page.get_by_label("Title *").input_value() == "By hand"
    assert not errors, errors


def test_create_a_draft_then_publish_it_from_the_list(live):
    ctx, base, db, gh = live
    page, errors = open_page(live, "/cms/content/?app=site&c=articles")
    page.get_by_role("heading", name="Articles").wait_for()
    page.get_by_text("By hand").first.wait_for()
    page.get_by_role("link", name="Add article").click()
    page.get_by_label("Title *").fill("Summer menu")
    page.get_by_role("button", name="Bold").click()  # the toolbar inserts markdown
    assert "**bold text**" in page.get_by_label("Body").input_value()
    page.get_by_role("button", name="Save draft").click()
    page.wait_for_url("**slug=summer-menu")
    saved = json.loads(gh.files["content/articles/summer-menu.json"])
    assert saved["status"] == "draft" and saved["title"] == "Summer menu"

    page.goto(base + "/cms/content/?app=site&c=articles")
    row = page.get_by_role("row", name="Summer menu")
    row.get_by_text("Draft").wait_for()
    row.get_by_role("button", name="Actions").click()
    page.get_by_role("menuitem", name="Publish now").click()
    page.get_by_text("Published", exact=True).first.wait_for()
    deadline = time.time() + 5
    while time.time() < deadline and json.loads(gh.files["content/articles/summer-menu.json"])["status"] != "published":
        time.sleep(0.1)
    assert json.loads(gh.files["content/articles/summer-menu.json"])["status"] == "published"
    assert not errors, errors


def test_history_shows_changes_and_undo_restores(live):
    ctx, base, db, gh = live
    page, errors = open_page(live, "/cms/activity/?app=site")
    page.get_by_text("Changes you can undo").wait_for()
    page.get_by_role("button", name="Undo").first.click()
    page.get_by_role("alertdialog").get_by_role("button", name="Undo").click()
    page.get_by_text("Undone").first.wait_for()
    assert "cms(revert)" in gh.commits[-1][0]
    assert not errors, errors


def test_statistics_cookie_banner_preview_compliance_and_media(live):
    page, errors = open_page(live, "/cms/stats/?app=site")
    page.get_by_role("heading", name="Statistics").wait_for()
    page.get_by_text("Conversion rate").first.wait_for()
    page.get_by_text("Traffic sources").wait_for()

    page.goto(live[1] + "/cms/cookies/?app=site")
    page.get_by_role("heading", name="Cookie banner").wait_for()
    frame = page.frame_locator("iframe[title=preview]")
    frame.locator("[data-mas-cookies]").wait_for(state="attached")
    page.wait_for_function("""() => { const f = document.querySelector('iframe[title=preview]');
        const h = f && f.contentDocument.querySelector('[data-mas-cookies]'); return !!(h && h.shadowRoot && h.shadowRoot.querySelector('.box')); }""")

    page.goto(live[1] + "/cms/compliance/?app=site")
    page.get_by_role("heading", name="Compliance").wait_for()
    page.get_by_role("button", name="Audit now").wait_for()

    page.goto(live[1] + "/cms/media/?app=site")
    page.get_by_role("heading", name="Media").wait_for()
    page.get_by_text("old.png").wait_for()
    assert not errors, errors


def test_inbox_bell_and_scheduling_with_the_time_picker(live):
    ctx, base, db, gh = live
    page, errors = open_page(live, "/cms/?app=site")
    page.get_by_role("heading", name="Dashboard").wait_for()
    page.get_by_role("button", name="Notifications").click()
    page.get_by_text("Inbox").first.wait_for()  # the Ruixen inbox, labels coming from Mas
    page.keyboard.press("Escape")

    page.goto(base + "/cms/entry/?app=site&c=articles&slug=hand")
    page.get_by_label("Title *").wait_for()
    page.get_by_label("Status").click()
    page.get_by_role("option", name="Scheduled").click()
    page.get_by_label("Publish on").wait_for()  # date + Ruixen time picker, filled with a default hour from now
    page.get_by_role("button", name="Schedule").wait_for()
    assert page.get_by_role("button", name="Schedule").is_enabled()
    page.get_by_role("button", name="Schedule").click()
    deadline = time.time() + 6
    while time.time() < deadline and not db.cms_scheduled(["site"]):
        time.sleep(0.1)
    scheduled = db.cms_scheduled(["site"])
    assert scheduled and scheduled[0]["slug"] == "hand"
    assert not errors, errors


def test_seo_panel_previews_and_writes_the_description_from_the_text(live):
    page, errors = open_page(live, "/cms/entry/?app=site&c=articles")
    page.get_by_label("Title *").wait_for()
    page.get_by_label("Title *").fill("Sourdough bread, baked every morning")
    page.get_by_label("Body").fill("## Why sourdough\n\nWe bake sourdough bread every morning in the heart of the city, with flour from a local mill, "
                                  "water and salt only. It keeps for days and tastes better each day.")
    page.get_by_placeholder("Focus keyword (optional)").fill("sourdough")
    panel = page.locator("div", has=page.get_by_role("heading", name="Search engines")).last
    page.get_by_text("The keyword is in the title").wait_for()
    assert page.get_by_text("Sourdough bread, baked every morning").first.is_visible()
    page.get_by_role("button", name="Write the description from the text").click()
    excerpt = page.get_by_label("Excerpt").input_value()
    assert excerpt.startswith("Why sourdough") or excerpt.startswith("We bake sourdough bread")
    assert 50 <= len(excerpt) <= 160
    page.get_by_text("Description of 50 to 160 characters").wait_for()
    assert not errors, errors


def real_png(size: int = 64) -> bytes:
    """A picture a browser can decode (the PNG of the CMS tests is only a stub)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    line = b"\x00" + b"".join(bytes((x * 4 % 256, 80, 200)) for x in range(size))  # filter byte + RGB pixels
    rows = line * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_profile_name_nickname_and_picture(live):
    picture = real_png()
    ctx, base, db, gh = live
    for old in ctx.pages:  # the pages of the earlier tests keep slow requests open and fill the browser's connections
        old.close()
    page, errors = open_page(live, "/settings/")
    page.get_by_placeholder("Jane Doe").wait_for()
    page.get_by_placeholder("Jane Doe").fill("Syan Code")
    page.get_by_placeholder("admin").fill("syan")
    page.get_by_role("button", name="Save").first.click()
    page.locator("[aria-label=syan]").wait_for()  # the page reloaded: the menu knows the nickname
    page.get_by_label("syan").click()
    page.get_by_text("Syan Code").first.wait_for()
    assert page.get_by_text("syan · @admin").is_visible()
    assert db.get_user("admin")["display_name"] == "Syan Code"
    # a real picture goes through the browser's canvas, then the server
    avatar = page.locator('img[src^="/api/account/avatar"]')
    page.set_input_files("input[type=file]", {"name": "me.png", "mimeType": "image/png", "buffer": picture})
    avatar.first.wait_for(timeout=15000)
    stored = db.get_avatar(db.get_user("admin")["id"])
    assert stored and stored[1] == "image/jpeg" and stored[0][:3] == b"\xff\xd8\xff"  # re-encoded and shrunk by the browser
    assert ctx.request.get(base + avatar.first.get_attribute("src")).status == 200
    page.get_by_role("button", name="Remove").click()
    avatar.first.wait_for(state="detached", timeout=15000)
    assert db.get_avatar(db.get_user("admin")["id"]) is None
    assert not errors, errors
