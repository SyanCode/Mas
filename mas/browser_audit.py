"""Audit with a real browser: what a visitor's Chrome actually does, JavaScript included.

The page is opened in a clean browser: every network request and cookie is recorded before anyone clicks
(trackers added by scripts show up here, unlike in the HTML), then a second clean visit clicks the banner's
reject button and checks that nothing that needs consent starts. The same visit measures basic accessibility
(images without text, unnamed fields and links, missing title, zoom blocked, low contrast) and speed.

Optional: needs ``pip install mas[browser]`` and a Chrome / Chromium on the server (``MAS_CHROMIUM`` points
to it when it is somewhere unusual). Without them Mas keeps using the HTML audit alone.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
from urllib.parse import urlsplit

from mas.compliance import TRACKING_COOKIES, _find_tracker, finding

log = logging.getLogger(__name__)

REJECT_RE = re.compile(r"tout refuser|refuser|reject all|decline|deny|ablehnen|alle ablehnen|rechazar|continuer sans accepter|"
                       r"continue without accepting", re.I)
SETTLE_MS = 3500

A11Y_JS = r"""() => {
  const out = {};
  const name = (el) => (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title') ||
    (el.innerText || '').trim() || (el.querySelector && el.querySelector('img[alt]:not([alt=""])') ? 'x' : '') || el.value || '');
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  out.imgNoAlt = [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt') && i.getAttribute('role') !== 'presentation' && i.getAttribute('aria-hidden') !== 'true' && visible(i)).length;
  out.unnamedLinks = [...document.querySelectorAll('a[href]')].filter(a => visible(a) && !name(a)).length;
  out.unnamedButtons = [...document.querySelectorAll('button, [role=button]')].filter(b => visible(b) && !name(b)).length;
  out.unlabeledFields = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]), select, textarea')]
    .filter(f => visible(f) && !f.getAttribute('aria-label') && !f.getAttribute('aria-labelledby') && !f.getAttribute('title') &&
      !(f.id && document.querySelector('label[for="' + CSS.escape(f.id) + '"]')) && !f.closest('label')).length;
  out.title = (document.title || '').trim();
  const h1 = document.querySelectorAll('h1'); out.h1 = h1.length;
  const levels = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => +h.tagName[1]);
  out.headingJump = levels.some((l, i) => i > 0 && l > levels[i - 1] + 1);
  const vp = document.querySelector('meta[name=viewport]'); const c = vp ? vp.content.toLowerCase() : '';
  out.zoomBlocked = /user-scalable\s*=\s*(no|0)/.test(c) || /maximum-scale\s*=\s*1(\.0)?\b/.test(c);
  const lum = (r, g, b) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e); if (s.backgroundImage !== 'none') return null; const c = parse(s.backgroundColor); if (c && c.a > 0.95) return c; } return { r: 255, g: 255, b: 255 }; };
  let low = 0, checked = 0;
  for (const el of document.querySelectorAll('p,li,a,span,h1,h2,h3,h4,h5,h6,button,label,td')) {
    if (checked >= 200) break;
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 2) || !visible(el)) continue;
    const s = getComputedStyle(el); const fg = parse(s.color); const bg = bgOf(el); if (!fg || !bg || fg.a < 0.95) continue;
    checked++;
    const l1 = lum(fg.r, fg.g, fg.b), l2 = lum(bg.r, bg.g, bg.b); const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const size = parseFloat(s.fontSize), bold = parseInt(s.fontWeight) >= 700; const large = size >= 24 || (size >= 18.66 && bold);
    if (ratio < (large ? 3 : 4.5)) low++;
  }
  out.lowContrast = low; out.contrastChecked = checked;
  return out;
}"""

PERF_JS = r"""() => new Promise((resolve) => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  let lcp = 0, cls = 0;
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true }); } catch (e) {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value; }).observe({ type: 'layout-shift', buffered: true }); } catch (e) {}
  setTimeout(() => resolve({ lcp, cls, load: nav.loadEventEnd || 0, dcl: nav.domContentLoadedEventEnd || 0 }), 400);
})"""


def find_chromium() -> str | None:
    env = os.environ.get("MAS_CHROMIUM")
    if env and os.path.exists(env):
        return env
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if path := shutil.which(name):
            return path
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))
    return found[-1] if found else None


def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return find_chromium() is not None


class BrowserAudit:
    def __init__(self, executable: str | None = None, extra_args: list[str] | None = None) -> None:
        self.executable = executable or find_chromium()
        self.extra_args = extra_args or []

    # one clean visit; ``act`` may click something after the first settle
    def _visit(self, browser, url: str, act=None) -> tuple[dict, object]:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US", ignore_https_errors=False)
        page = ctx.new_page()
        requests: list[tuple[str, str, float]] = []
        sizes: dict[str, int] = {}
        page.on("request", lambda r: requests.append((r.url, r.resource_type, len(requests))))
        page.on("response", lambda r: sizes.__setitem__(r.url, int(r.headers.get("content-length", "0") or 0)))
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(SETTLE_MS)
        before = len(requests)
        cookies_before = [c["name"] for c in ctx.cookies()]
        acted = None
        if act:
            acted = act(page)
            page.wait_for_timeout(SETTLE_MS)
        out = {"requests": [r[0] for r in requests], "before": before, "cookies_before": cookies_before,
               "cookies_after": [c["name"] for c in ctx.cookies()], "acted": acted, "sizes": sizes}
        if not act:
            out["a11y"] = page.evaluate(A11Y_JS)
            out["perf"] = page.evaluate(PERF_JS)
            out["banner"] = self._banner(page)
        return out, ctx

    @staticmethod
    def _banner(page) -> dict:
        """Is there a banner, and a reject button on its first layer?"""
        mas = page.evaluate("() => { const h = document.querySelector('[data-mas-cookies]'); const r = h && h.shadowRoot; return !!(r && r.querySelector('.box')); }")
        reject = page.evaluate("() => { const h = document.querySelector('[data-mas-cookies]'); const r = h && h.shadowRoot; return !!(r && r.querySelector('[data-a=reject]')); }") if mas else False
        if not mas:
            reject = page.get_by_role("button", name=REJECT_RE).count() > 0 or page.get_by_role("link", name=REJECT_RE).count() > 0
        accept = mas or page.get_by_role("button", name=re.compile(r"accept|accepter|tout accepter|agree|ok|zustimmen|aceptar", re.I)).count() > 0
        return {"mas": bool(mas), "reject": bool(reject), "present": bool(mas or reject or accept)}

    @staticmethod
    def _click_reject(page) -> bool:
        if page.evaluate("() => { const h = document.querySelector('[data-mas-cookies]'); const r = h && h.shadowRoot; const b = r && r.querySelector('[data-a=reject]'); if (b) { b.click(); return true; } return false; }"):
            return True
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=REJECT_RE)
            if loc.count():
                loc.first.click(timeout=5000)
                return True
        return False

    def run(self, base_url: str) -> dict:
        """Findings (same shape as the HTML audit) plus the measures; raises on browser failure."""
        from playwright.sync_api import sync_playwright
        host = urlsplit(base_url).hostname or ""
        out: list[dict] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=self.executable, headless=True,
                                         args=["--no-sandbox", "--disable-dev-shm-usage", *self.extra_args])
            try:
                first, ctx = self._visit(browser, base_url)
                out += self._before_consent(base_url, host, first)
                out += self._accessibility(base_url, first["a11y"])
                out += self._performance(base_url, first)
                banner = first["banner"]
                ctx.close()
                if banner["present"]:
                    if not banner["reject"]:
                        out.append(finding("refuse-missing", "warning", "consent", "No \"reject all\" button on the first layer of the banner",
                                           "Refusing must be as easy as accepting (CNIL): one click, at the same level as \"accept\".",
                                           "Add a \"Reject all\" button next to \"Accept all\" (the Mas banner has one)."))
                    else:
                        second, ctx2 = self._visit(browser, base_url, self._click_reject)
                        out += self._after_refusal(base_url, host, first, second)
                        ctx2.close()
            finally:
                browser.close()
        return {"findings": out, "banner": first["banner"], "perf": first["perf"], "a11y": first["a11y"]}

    # -- reading the visits ----------------------------------------------------------------------------------

    def _before_consent(self, url: str, host: str, v: dict) -> list[dict]:
        out = []
        seen = {}
        for ref in v["requests"][: v["before"]]:
            if (t := _find_tracker(ref)) and t[2] in ("analytics", "marketing", "embed") and urlsplit(ref).hostname != host:
                seen.setdefault(t[1], t)
        for name, t in sorted(seen.items()):
            out.append(finding(f"tracker-{re.sub(r'[^a-z0-9]+', '-', name.lower())}", "critical", "trackers", f"{name} loads before consent",
                               f"It is loaded as soon as the page opens ({t[2]}, servers: {t[3]}). Consent must come first.",
                               'Mark it type="text/plain" data-mas-consent="' + ("analytics" if t[2] == "analytics" else "marketing") +
                               '" so the cookie banner starts it after consent.', url))
        tracking = [c for c in v["cookies_before"] if TRACKING_COOKIES.search(c)]
        if tracking:
            out.append(finding("cookies-tracking", "critical", "cookies", "Tracking cookies are set before any consent",
                               "On the very first visit the server sends: " + ", ".join(tracking) + ".",
                               "Load the tool only after consent (see the cookie banner) or remove it.", url))
        return out

    def _after_refusal(self, url: str, host: str, first: dict, second: dict) -> list[dict]:
        if not second["acted"]:
            return []
        out = []
        started = {}
        for ref in second["requests"][second["before"]:]:
            if (t := _find_tracker(ref)) and t[2] in ("analytics", "marketing") and urlsplit(ref).hostname != host:
                started.setdefault(t[1], t)
        cookies = [c for c in second["cookies_after"] if TRACKING_COOKIES.search(c)]
        names = sorted(started)
        if names or cookies:
            out.append(finding("refusal-ignored", "critical", "consent", "Services still run after the visitor refuses",
                               "After clicking \"reject\": " + "; ".join(filter(None, [", ".join(names), ("cookies " + ", ".join(cookies)) if cookies else ""])) + ".",
                               "Make the reject button really block these services (load them only after an explicit accept).", url))
        else:
            out.append(finding("refusal-respected", "pass", "consent", "Refusing the banner stops the trackers",
                               "After clicking \"reject\", no analytics or advertising request and no tracking cookie."))
        return out

    def _accessibility(self, url: str, a: dict) -> list[dict]:
        out = []

        def add(fid, level, title, detail, fix):
            out.append(finding(fid, level, "accessibility", title, detail, fix, url))
        if a["imgNoAlt"]:
            add("a11y-alt", "warning", f"{a['imgNoAlt']} image(s) without alternative text", "Screen readers cannot describe them (RGAA / EAA).", 'Add alt="…" (alt="" for decoration).')
        if a["unlabeledFields"]:
            add("a11y-labels", "warning", f"{a['unlabeledFields']} form field(s) without a label", "People using a screen reader do not know what to type.", "Add a <label for> or aria-label to each field.")
        if a["unnamedLinks"] or a["unnamedButtons"]:
            add("a11y-names", "warning", f"{a['unnamedLinks'] + a['unnamedButtons']} link(s) or button(s) without a name", "An icon-only link or button says nothing to a screen reader.", "Add text or an aria-label.")
        if not a["title"]:
            add("a11y-title", "warning", "The page has no title", "The title is the first thing a screen reader announces and shows in search results.", "Add a <title>.")
        if a["zoomBlocked"]:
            add("a11y-zoom", "warning", "Zooming is blocked", "user-scalable=no / maximum-scale=1 prevents people with low vision from enlarging the page.", "Remove it from the viewport meta tag.")
        if a["lowContrast"]:
            add("a11y-contrast", "warning", f"{a['lowContrast']} text element(s) with low contrast", f"Out of {a['contrastChecked']} checked: contrast under 4.5:1 (3:1 for large text).", "Darken the text or lighten the background.")
        if a["h1"] != 1:
            add("a11y-h1", "info", "The page should have exactly one main heading (h1)", f"Found {a['h1']}.", "Use one <h1> that names the page.")
        if a["headingJump"]:
            add("a11y-headings", "info", "Heading levels skip a step", "For example an h2 followed by an h4.", "Go down one level at a time.")
        if not out:
            out.append(finding("a11y-ok", "pass", "accessibility", "No basic accessibility problem found", "Automatic checks only cover part of the rules: test with a keyboard and a screen reader too.", "", url))
        return out

    def _performance(self, url: str, v: dict) -> list[dict]:
        out = []
        p, total = v["perf"], sum(v["sizes"].values())
        big = [u for u, s in v["sizes"].items() if s > 500_000 and re.search(r"\.(png|jpe?g|webp|avif|gif)(\?|$)", u, re.I)]
        if p["lcp"] > 4000:
            out.append(finding("perf-lcp", "warning", "performance", f"Slow main content ({p['lcp'] / 1000:.1f} s)", "Google asks for under 2.5 s; above 4 s is poor.", "Compress images, serve them in WebP/AVIF, avoid blocking scripts.", url))
        elif p["lcp"] > 2500:
            out.append(finding("perf-lcp", "info", "performance", f"Main content a bit slow ({p['lcp'] / 1000:.1f} s)", "Under 2.5 s is good.", "Compress the largest image.", url))
        if p["cls"] > 0.25:
            out.append(finding("perf-cls", "warning", "performance", f"The layout jumps while loading (CLS {p['cls']:.2f})", "Above 0.25 is poor.", "Give images and embeds a width and height.", url))
        if total > 3_000_000:
            out.append(finding("perf-weight", "warning", "performance", f"The page weighs {total / 1_000_000:.1f} MB", "Heavy pages are slow on mobile and cost energy.", "Compress images and drop unused scripts.", url))
        if big:
            out.append(finding("perf-images", "warning", "performance", f"{len(big)} image(s) over 500 KB", ", ".join(u.rsplit('/', 1)[-1][:40] for u in big[:4]), "Resize and compress them.", url))
        if len(v["requests"]) > 120:
            out.append(finding("perf-requests", "info", "performance", f"{len(v['requests'])} requests to open the page", "Many requests slow the first visit.", "Bundle scripts and styles, load below-the-fold content later.", url))
        if not out:
            out.append(finding("perf-ok", "pass", "performance", f"The page loads quickly (main content in {p['lcp'] / 1000:.1f} s)", "", "", url))
        return out
