"""Automatic GDPR audit of a published site, with the site served from memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.compliance import Auditor, Compliance, Response
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

HEAD = [("content-type", "text/html"), ("strict-transport-security", "max-age=1"),
        ("content-security-policy", "default-src 'self'"), ("x-content-type-options", "nosniff"),
        ("referrer-policy", "same-origin")]

LEGAL = ("<html><body>Mentions légales. Éditeur : Dupont SARL, SIRET 123. Directeur de la publication : M. Dupont. "
         "Hébergeur : Mas Hosting. Contact : a@b.fr</body></html>")
PRIVACY = ("<html><body>Le responsable de traitement est Dupont. Finalités : répondre aux messages. Durée de conservation : "
           "3 ans. Vos droits : accès, rectification, effacement. Autorité de contrôle : la CNIL. Destinataires : notre hébergeur."
           "</body></html>")


def site(pages: dict[str, tuple[str, list]], plain_redirect=True):
    def fetch(url: str, follow: bool = True) -> Response:
        if url.startswith("http://"):
            return Response(url, 301 if plain_redirect else 200, [("location", "https://" + url[7:])])
        body, headers = pages.get(url, (None, []))
        if body is None:
            return Response(url, 404)
        return Response(url, 200, headers or HEAD, body.encode())
    return fetch


BAD_HOME = ('<html><head><script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ"></script>'
            '<link href="https://fonts.googleapis.com/css?family=Inter" rel="stylesheet"></head><body>'
            '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
            '<form action="/send"><input type="email" name="email"><button>Send</button></form></body></html>')

GOOD_HOME = ('<html lang="fr"><head><script defer src="https://mas.example.com/cookies.js"></script>'
             '<script type="text/plain" data-mas-consent="analytics" src="https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ"></script>'
             '</head><body><a href="/mentions-legales">Mentions légales</a> <a href="/confidentialite">Politique de confidentialité</a>'
             '<form action="/send"><input type="email" name="email"><p>Vos données : voir la <a href="/confidentialite">politique de confidentialité</a></p></form>'
             '</body></html>')


def ids(result, level=None):
    return {f["id"] for f in result["findings"] if level in (None, f["level"])}


def test_a_site_full_of_problems_is_blocking():
    fetch = site({"https://shop.example.com/": (BAD_HOME, HEAD + [("set-cookie", "_ga=GA1.2; Path=/")])}, plain_redirect=False)
    result = Auditor(fetch).audit("shop.example.com")
    critical = ids(result, "critical")
    assert {"tracker-google-tag-manager", "tracker-youtube-player", "no-banner", "privacy-missing", "legal-missing",
            "cookies-tracking"} <= critical
    assert {"font-google", "form-info", "https-redirect"} <= ids(result, "warning")
    assert result["verdict"] == "blocking" and result["score"] < 40
    assert all(f["fix"] for f in result["findings"] if f["level"] in ("critical", "warning"))


def test_a_clean_site_passes_and_gated_trackers_are_not_flagged():
    fetch = site({"https://shop.example.com/": (GOOD_HOME, None), "https://shop.example.com/mentions-legales": (LEGAL, None),
                  "https://shop.example.com/confidentialite": (PRIVACY, None)})
    banner = {"enabled": True, "policy_url": "/confidentialite"}
    result = Auditor(fetch, mas_banner=lambda host: banner).audit("shop.example.com")
    assert result["verdict"] == "ok" and result["score"] == 100, result["findings"]
    assert {"legal-ok", "privacy-ok"} <= ids(result, "pass")


def test_incomplete_legal_pages_and_a_disabled_banner_are_warned():
    home = GOOD_HOME.replace("data-mas-consent=\"analytics\" ", "data-mas-consent=\"analytics\" ")
    fetch = site({"https://shop.example.com/": (home, None), "https://shop.example.com/mentions-legales": ("<html>Mentions légales</html>", None),
                  "https://shop.example.com/confidentialite": ("<html>Nous aimons vos données.</html>", None)})
    result = Auditor(fetch, mas_banner=lambda host: {"enabled": False, "policy_url": ""}).audit("shop.example.com")
    assert {"legal-incomplete", "privacy-incomplete", "banner-off"} <= ids(result, "warning")
    assert result["verdict"] == "attention"


def test_unreachable_and_invalid_certificate():
    def tls(url, follow=True):
        return Response(url, 0, error="tls")
    assert ids(Auditor(tls).audit("x.example.com"), "critical") == {"https-invalid"}
    assert ids(Auditor(lambda u, follow=True: Response(u, 0, error="network")).audit("x.example.com"), "critical") == {"site-unreachable"}


def test_new_findings_are_flagged_and_audits_follow_releases(tmp_path):
    db = Database(tmp_path / "mas.db")
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com")
    pages = {"https://shop.example.com/": (GOOD_HOME, None), "https://shop.example.com/mentions-legales": (LEGAL, None),
             "https://shop.example.com/confidentialite": (PRIVACY, None)}
    comp = Compliance(db, Auditor(lambda url, follow=True: site(pages)(url, follow),
                                  mas_banner=lambda h: {"enabled": True, "policy_url": "/c"}))
    app = db.get_app("shop")
    assert comp.tick() == []  # nothing deployed yet: nothing to audit

    # a release goes live: audited once it has settled, and only once
    rid = db.create_release("shop", "push", "alice", status="live")
    db.update_release(rid, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    now = datetime.now(timezone.utc).timestamp()
    assert comp.tick(now + 5) == []  # let nginx settle
    assert comp.tick(now + 120) == ["shop"] and comp.tick(now + 130) == []
    first = comp.latest("shop")
    assert first["verdict"] == "ok" and first["release_id"] == rid and first["trigger"] == "release"
    assert not any(f.get("new") for f in first["findings"])

    # the next change adds a tracker: the new problem is flagged and the score drops
    pages["https://shop.example.com/"] = (BAD_HOME.replace("</body>", '<a href="/mentions-legales">Mentions légales</a>'
                                                           '<a href="/confidentialite">Confidentialité</a></body>'), HEAD)
    rid2 = db.create_release("shop", "cms", "marie", status="live")
    db.update_release(rid2, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    assert comp.tick(now + 400) == ["shop"]
    second = comp.latest("shop")
    assert any(f["new"] and f["level"] == "critical" for f in second["findings"])
    assert second["score"] < first["score"]
    assert [h["score"] for h in comp.history("shop")] == [second["score"], first["score"]]
    assert comp.tick(now + 60 * 60 * 24 * 9) == ["shop"]  # a week later: looked at again
    assert any(e["action"] == "cms-compliance" for e in db.history(None, 10))


def test_api_runs_and_returns_the_audit(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com",
                  installation_id=1)
    db.create_app("bare", name="Bare", repo="acme/bare", branch="main", type="static", installation_id=1)
    api = create_app(host, Config(cookie_secure=False), db, background=False)
    pages = {"https://shop.example.com/": (GOOD_HOME, None), "https://shop.example.com/mentions-legales": (LEGAL, None),
             "https://shop.example.com/confidentialite": (PRIVACY, None)}
    api.state.compliance.auditor = Auditor(lambda url, follow=True: site(pages)(url, follow),
                                           mas_banner=lambda h: {"enabled": True, "policy_url": "/c"})
    client = TestClient(api)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    h = {"x-csrf-token": csrf}
    r = client.get("/api/cms/sites/shop/compliance")
    assert r.status_code == 200 and r.json()["latest"] is None and "register" in r.json()["manual"]
    assert client.get("/api/cms/dashboard").json()["sites"][0]["compliance"] is None
    run = client.post("/api/cms/sites/shop/compliance/run", headers=h)
    assert run.status_code == 200 and run.json()["latest"]["verdict"] == "ok" and run.json()["latest"]["trigger"] == "manual"
    dash = {s["id"]: s for s in client.get("/api/cms/dashboard").json()["sites"]}
    assert dash["shop"]["compliance"]["score"] == 100
    assert client.post("/api/cms/sites/bare/compliance/run", headers=h).status_code == 400  # no domain
    assert client.post("/api/cms/sites/nope/compliance/run", headers=h).status_code == 404


def test_every_audit_text_can_be_translated():
    """The interface translates the English texts of mas/compliance.py: none may be missing from the list."""
    import ast
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    listed = (root / "web" / "lib" / "complianceTexts.ts").read_text(encoding="utf-8")
    missing = []
    for name in ("compliance.py", "browser_audit.py"):
        tree = ast.parse((root / "mas" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("finding", "add"):
                start = 3 if node.func.id == "finding" else 2
                for arg in node.args[start:] + [k.value for k in node.keywords if k.arg in ("detail", "fix")]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value \
                            and arg.value != '<html lang="fr">' and json.dumps(arg.value, ensure_ascii=False) not in listed:
                        missing.append(arg.value)
    assert not missing, f"add to web/lib/complianceTexts.ts and the locales: {missing[:3]}"


def test_browser_findings_are_merged_and_a_failure_is_not_fatal(tmp_path):
    db = Database(tmp_path / "mas.db")
    db.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="static", domain="shop.example.com")
    pages = {"https://shop.example.com/": (GOOD_HOME, None), "https://shop.example.com/mentions-legales": (LEGAL, None),
             "https://shop.example.com/confidentialite": (PRIVACY, None)}
    auditor = Auditor(lambda url, follow=True: site(pages)(url, follow), mas_banner=lambda h: {"enabled": True, "policy_url": "/c"})
    app = db.get_app("shop")

    class Browser:
        def run(self, url):
            assert url == "https://shop.example.com/"
            return {"banner": {"present": True}, "perf": {"lcp": 900}, "findings": [
                {"id": "refusal-ignored", "level": "critical", "category": "consent", "title": "Services still run after the visitor refuses",
                 "detail": "d", "fix": "f", "where": ""},
                {"id": "a11y-alt", "level": "warning", "category": "accessibility", "title": "2 image(s) without alternative text",
                 "detail": "d", "fix": "f", "where": ""},
                {"id": "legal-ok", "level": "pass", "category": "legal", "title": "duplicate of an HTML finding", "detail": "", "fix": "", "where": "x"}]}
    result = Compliance(db, auditor, browser=Browser()).run(app, None, "manual")
    assert result["browser"]["used"] is True and result["verdict"] == "blocking"
    assert {"refusal-ignored", "a11y-alt"} <= ids(result) and sum(1 for f in result["findings"] if f["id"] == "legal-ok") == 1
    assert result["score"] == 100 - 20 - 6 and "detected" in result

    class Broken:
        def run(self, url):
            raise RuntimeError("chrome crashed")
    result = Compliance(db, auditor, browser=Broken()).run(app, None, "manual")
    assert result["verdict"] == "ok" and "browser-failed" in ids(result, "info")  # the HTML audit still stands
    assert Compliance(db, auditor, browser=None).run(app, None, "manual")["browser"] == {"used": False}
