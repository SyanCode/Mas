"""What Mas adds to the nginx site of an app from the CMS: redirects, robots.txt and a sitemap.

Everything the person types is checked against a narrow alphabet before it reaches an nginx file: a path
cannot carry a quote, a semicolon, a brace or a variable. robots.txt and sitemap.xml are written as files and
served with ``alias``, so their text is never part of a configuration line."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable
from xml.sax.saxutils import escape

from mas.cms import CmsError

DIR = "/etc/nginx/mas-site"
MAX_REDIRECTS = 200
MAX_URLS = 5000
CODES = (301, 302)
PATH_RE = re.compile(r"^/[A-Za-z0-9._~%/-]{0,300}\*?$")
ABS_RE = re.compile(r"^https?://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/[A-Za-z0-9._~%/?=&#+:@,*-]{0,300})?$")
TEMPLATE_RE = re.compile(r"^/[A-Za-z0-9._~%/-]{0,200}\{slug\}[A-Za-z0-9._~%/-]{0,50}$")
ROBOTS_LINE_RE = re.compile(r"^[\x20-\x7e]{0,300}$")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class RulesError(CmsError):
    pass


def default_config() -> dict:
    return {"redirects": [], "robots": {"enabled": False, "text": ""},
            "sitemap": {"enabled": False, "pages": ["/"], "collections": {}}}


def clean_redirects(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_REDIRECTS:
        raise RulesError(f"redirects: a list of at most {MAX_REDIRECTS}")
    out, seen = [], set()
    for i, r in enumerate(raw, 1):
        if not isinstance(r, dict):
            raise RulesError(f"redirect {i}: from, to and code")
        src, dst, code = str(r.get("from", "")).strip(), str(r.get("to", "")).strip(), r.get("code", 301)
        if not PATH_RE.fullmatch(src) or src == "/*" or "//" in src:
            raise RulesError(f"redirect {i}: “from” is a path like /old-page (or /blog/* for a whole section)")
        if not (PATH_RE.fullmatch(dst) and "//" not in dst or ABS_RE.fullmatch(dst)):
            raise RulesError(f"redirect {i}: “to” is a path (/new-page) or a full address (https://…)")
        if code not in CODES:
            raise RulesError(f"redirect {i}: code 301 (permanent) or 302 (temporary)")
        if src.endswith("*") != dst.endswith("*") and src.endswith("*"):
            raise RulesError(f"redirect {i}: a section (…/*) goes to a section (…/*)")
        if src.endswith("*") and not src.endswith("/*"):
            raise RulesError(f"redirect {i}: a section ends with /*")
        if src == dst:
            raise RulesError(f"redirect {i}: it points to itself")
        if src in seen:
            raise RulesError(f"redirect {i}: {src} is already redirected")
        seen.add(src)
        out.append({"from": src, "to": dst, "code": code})
    return out


def clean_robots(raw) -> dict:
    if not isinstance(raw, dict):
        raise RulesError("robots: enabled and text")
    text = str(raw.get("text", "")).replace("\r\n", "\n").strip("\n")
    lines = text.split("\n") if text else []
    if len(lines) > 200 or any(not ROBOTS_LINE_RE.fullmatch(l) for l in lines):
        raise RulesError("robots.txt: plain text, 200 lines at most")
    return {"enabled": bool(raw.get("enabled")), "text": "\n".join(lines)}


def clean_sitemap(raw) -> dict:
    if not isinstance(raw, dict):
        raise RulesError("sitemap: enabled, pages and collections")
    pages = raw.get("pages") or []
    if not isinstance(pages, list) or len(pages) > 500 or any(not PATH_RE.fullmatch(str(p)) or str(p).endswith("*") for p in pages):
        raise RulesError("sitemap pages: paths like /contact")
    cols = raw.get("collections") or {}
    if not isinstance(cols, dict) or len(cols) > 20:
        raise RulesError("sitemap collections: {collection: /blog/{slug}}")
    for cid, tpl in cols.items():
        if not isinstance(cid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", cid) or not TEMPLATE_RE.fullmatch(str(tpl)):
            raise RulesError(f"sitemap: the address of “{cid}” looks like /blog/{{slug}}")
    return {"enabled": bool(raw.get("enabled")), "pages": list(dict.fromkeys(str(p) for p in pages)),
            "collections": {k: str(v) for k, v in cols.items()}}


def clean(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RulesError("invalid settings")
    return {"redirects": clean_redirects(raw.get("redirects", [])), "robots": clean_robots(raw.get("robots", {})),
            "sitemap": clean_sitemap(raw.get("sitemap", {}))}


def default_robots(domain: str, sitemap: bool) -> str:
    return "User-agent: *\nAllow: /\n" + (f"\nSitemap: https://{domain}/sitemap.xml\n" if sitemap else "")


def nginx_rules(app_id: str, cfg: dict) -> str:
    """The lines added to the site's snippet (the vhost includes it)."""
    out = []
    for r in cfg["redirects"]:
        flag = "permanent" if r["code"] == 301 else "redirect"
        if r["from"].endswith("/*"):
            prefix = r["from"][:-1]  # /blog/
            target = r["to"][:-1] + "$1" if r["to"].endswith("*") else r["to"]
            regex = re.escape(prefix).replace("\\/", "/").replace("\\-", "-").replace("\\~", "~").replace("\\%", "%")
            out.append(f"location ^~ {prefix} {{ rewrite ^{regex}(.*)$ {target} {flag}; }}")
        else:
            out.append(f"location = {r['from']} {{ return {r['code']} {r['to']}; }}")
    if cfg["robots"]["enabled"]:
        out.append(f"location = /robots.txt {{ alias {DIR}/{app_id}/robots.txt; default_type text/plain; }}")
    if cfg["sitemap"]["enabled"]:
        out.append(f"location = /sitemap.xml {{ alias {DIR}/{app_id}/sitemap.xml; default_type application/xml; }}")
    return ("# Generated by Mas (CMS: redirects, robots.txt, sitemap). Changes are overwritten.\n" + "\n".join(out) + "\n") if out else ""


def sitemap_xml(domain: str, urls: list[tuple[str, str | None]]) -> str:
    rows = []
    for path, lastmod in urls[:MAX_URLS]:
        rows.append(f"  <url><loc>{escape(f'https://{domain}{path}')}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n"


def collect_urls(cfg: dict, collections: list[dict]) -> list[tuple[str, str | None]]:
    """Static pages, then one address per published entry of the chosen collections."""
    urls: list[tuple[str, str | None]] = [(p, None) for p in cfg["sitemap"]["pages"]]
    for c in collections:
        tpl = cfg["sitemap"]["collections"].get(c["id"])
        if not tpl:
            continue
        sort = c.get("sort_field")
        for e in c.get("entries", []):
            if c.get("drafts") and e.get("status", "published") != "published":
                continue
            slug = str(e.get("slug", ""))
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", slug):
                continue
            m = DATE_RE.match(str(e.get(sort, ""))) if sort else None
            urls.append((tpl.replace("{slug}", slug), m.group(1) if m else None))
    return list(dict.fromkeys(urls))


class SiteRules:
    """Settings per site (kept in the database) and their effect on the nginx site of the app."""

    def __init__(self, db, host, maintenance, cms) -> None:
        self.db = db
        self.host = host
        self.maintenance = maintenance
        self.cms = cms
        maintenance.extra_rules = self.snippet  # the maintenance module writes the one file the vhost includes

    def get(self, app_id: str) -> dict:
        raw = self.db.get_setting(f"siterules:{app_id}")
        cfg = default_config()
        if raw:
            try:
                cfg = {**cfg, **json.loads(raw)}
            except json.JSONDecodeError:
                pass
        return cfg

    def snippet(self, app: dict) -> str:
        return nginx_rules(app["id"], self.get(app["id"]))

    def state(self, app_id: str) -> dict:
        app = self.db.get_app(app_id)
        if app is None:
            raise RulesError("unknown site")
        domain = app.get("domain")
        return {**self.get(app_id), "domain": domain, "supported": bool(domain) and self.maintenance.supported(app),
                "default_robots": default_robots(domain or "example.com", self.get(app_id)["sitemap"]["enabled"])}

    def put(self, app_id: str, raw: dict) -> dict:
        app = self.db.get_app(app_id)
        if app is None:
            raise RulesError("unknown site")
        cfg = clean(raw)
        if not self.maintenance.supported(app):
            raise RulesError("Mas writes the nginx site of apps it deploys, with a domain: this one is served another way")
        if cfg["robots"]["enabled"] and not cfg["robots"]["text"]:
            cfg["robots"]["text"] = default_robots(app["domain"], cfg["sitemap"]["enabled"])
        previous = self.db.get_setting(f"siterules:{app_id}")
        self.db.set_setting(f"siterules:{app_id}", json.dumps(cfg))
        try:
            self.refresh(app_id)
        except Exception:
            self.db.set_setting(f"siterules:{app_id}", previous)  # nginx refused it: keep what worked
            raise
        return self.state(app_id)

    def refresh(self, app_id: str) -> None:
        """Write robots.txt and sitemap.xml, then the snippet; nginx is tested and reloaded."""
        app = self.db.get_app(app_id)
        if app is None or not app.get("domain"):
            return
        cfg = self.get(app_id)
        folder = f"{DIR}/{app_id}"
        if cfg["robots"]["enabled"] or cfg["sitemap"]["enabled"]:
            self.host.makedirs(folder)
        if cfg["robots"]["enabled"]:
            self.host.write_text(f"{folder}/robots.txt", cfg["robots"]["text"].rstrip("\n") + "\n", mode=0o644)
        if cfg["sitemap"]["enabled"]:
            collections = []
            if cfg["sitemap"]["collections"]:
                try:
                    collections = self.cms.load(app_id)["collections"]
                except CmsError:
                    collections = []  # the sitemap of the pages is still worth writing
            urls = collect_urls(cfg, collections)
            self.host.write_text(f"{folder}/sitemap.xml", sitemap_xml(app["domain"], urls), mode=0o644)
        self.maintenance.apply()

    def tick(self, now: float | None = None) -> list[str]:
        """The sitemaps follow the entries: rewritten at most every 15 minutes (and after each CMS commit)."""
        done = []
        for app in self.db.list_apps():
            cfg = self.get(app["id"])
            if not cfg["sitemap"]["enabled"] or not cfg["sitemap"]["collections"]:
                continue
            key = f"siterules_at:{app['id']}"
            last = float(self.db.get_setting(key) or 0)
            stamp = now if now is not None else datetime.now(timezone.utc).timestamp()
            if stamp - last < 900:
                continue
            self.db.set_setting(key, str(stamp))
            try:
                self.refresh(app["id"])
                done.append(app["id"])
            except Exception:  # noqa: BLE001 - one site must not stop the others
                continue
        return done
