"""Audit with a real Chromium against pages served locally (skipped when no browser is installed)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mas.browser_audit import BrowserAudit, available

pytestmark = pytest.mark.skipif(not available(), reason="needs playwright and a Chromium")

BANNER = ('<div id="b"><button id="acc">Accept all</button><button id="rej">Tout refuser</button></div>')
LOAD_GTM = "var s=document.createElement('script');s.src='http://www.googletagmanager.com/gtag/js';document.head.appendChild(s);"

PAGES = {
    # a tracker added by JavaScript at load: invisible in the HTML, visible to a real browser
    "/injected": f'<!doctype html><html lang="en"><head><title>Shop</title></head><body><h1>Shop</h1>{BANNER}<script>{LOAD_GTM}</script></body></html>',
    # clean until the visitor clicks "reject": then the tracker starts anyway
    "/liar": f'<!doctype html><html lang="en"><head><title>Shop</title></head><body><h1>Shop</h1>{BANNER}<script>document.getElementById("rej").onclick=function(){{{LOAD_GTM}}}</script></body></html>',
    # honest: nothing loads, and the reject button does nothing
    "/honest": f'<!doctype html><html lang="en"><head><title>Shop</title></head><body><h1>Shop</h1>{BANNER}<script>document.getElementById("rej").onclick=function(){{document.getElementById("b").remove()}}</script></body></html>',
    # a banner with no way to refuse, and several accessibility problems
    "/noreject": '<!doctype html><html><head><meta name="viewport" content="width=device-width, user-scalable=no"></head><body>'
                 '<h1>A</h1><h1>B</h1><h4>Skipped</h4><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="><input type="text">'
                 '<a href="/x"><svg width="20" height="20"></svg></a>'
                 '<p style="color:#bbb;background:#fff">pale text that is hard to read</p><div id="b"><button>Accept all</button></div></body></html>',
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if "gtag" in self.path:
            self.send_response(200); self.send_header("Content-Type", "text/javascript"); self.end_headers()
            self.wfile.write(b"window.gtagLoaded=true;")
            return
        body = PAGES.get(self.path.split("?")[0], "<html><body>404</body></html>").encode()
        self.send_response(200 if self.path in PAGES else 404)
        self.send_header("Content-Type", "text/html")
        if self.path == "/injected":
            self.send_header("Set-Cookie", "_ga=GA1.2.3; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()


def run(port: int, path: str) -> dict:
    audit = BrowserAudit(extra_args=[f"--host-resolver-rules=MAP www.googletagmanager.com 127.0.0.1:{port}"])
    result = audit.run(f"http://127.0.0.1:{port}{path}")
    result["ids"] = {f["id"]: f["level"] for f in result["findings"]}
    return result


def test_a_tracker_added_by_javascript_and_its_cookie_are_seen(site):
    r = run(site, "/injected")
    assert r["ids"]["tracker-google-tag-manager"] == "critical" and r["ids"]["cookies-tracking"] == "critical"
    assert r["banner"]["present"] and r["banner"]["reject"]


def test_a_reject_button_that_does_not_stop_the_tracker_is_caught(site):
    r = run(site, "/liar")
    assert "tracker-google-tag-manager" not in r["ids"]  # nothing before the click
    assert r["ids"]["refusal-ignored"] == "critical"


def test_an_honest_banner_passes(site):
    r = run(site, "/honest")
    assert r["ids"].get("refusal-respected") == "pass" and "refusal-ignored" not in r["ids"]
    assert r["ids"].get("a11y-ok") == "pass" and r["ids"].get("perf-ok") == "pass"


def test_missing_reject_and_accessibility_problems(site):
    r = run(site, "/noreject")
    ids = r["ids"]
    assert ids["refuse-missing"] == "warning"
    assert {"a11y-alt", "a11y-labels", "a11y-names", "a11y-title", "a11y-zoom", "a11y-contrast"} <= set(ids)
    assert ids["a11y-h1"] == "info" and ids["a11y-headings"] == "info"
