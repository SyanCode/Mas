"""A look at how the live site presents itself to search engines: titles, descriptions, headings, images,
social previews, robots.txt and the sitemap. Same crawl rules as the compliance audit (a few pages, HTTPS only)."""

from __future__ import annotations

import re
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from mas.compliance import Response, http_fetch

MAX_PAGES = 8
TITLE = (15, 60)
DESCRIPTION = (50, 160)


class SeoPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.h1: list[str] = []
        self.images = 0
        self.no_alt = 0
        self.links: list[str] = []
        self.paragraphs: list[str] = []
        self._in = ""
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title" and not self.title:
            self._in = "title"
        elif tag == "h1":
            self._in = "h1"
            self.h1.append("")
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta.setdefault(key, a.get("content", ""))
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href", "")
        elif tag == "img":
            self.images += 1
            if "alt" not in a:
                self.no_alt += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._in = "p"
            self.paragraphs.append("")

    def handle_endtag(self, tag):
        if tag in ("title", "h1", "p"):
            self._in = ""
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "h1" and self.h1:
            self.h1[-1] += data
        elif self._in == "p" and self.paragraphs:
            self.paragraphs[-1] += data


def read(html: str) -> SeoPage:
    page = SeoPage()
    try:
        page.feed(html)
        page.close()
    except Exception:  # noqa: BLE001 - broken HTML is read as far as it goes
        pass
    page.title = " ".join(page.title.split())
    return page


def _cut(text: str, limit: int) -> str:
    """The text cut after a whole sentence or word, at most ``limit`` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    stop = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if stop >= 50:
        return head[: stop + 1]
    return head[: head.rfind(" ")].rstrip(" ,;:-–|") if " " in head else head


def suggest(p: SeoPage, site_name: str = "") -> dict:
    """Wording Mas would put in a title and a description, taken from the page itself."""
    out: dict = {}
    desc = p.meta.get("description", "").strip()
    if not DESCRIPTION[0] <= len(desc) <= DESCRIPTION[1]:
        body = next((" ".join(x.split()) for x in p.paragraphs if len(" ".join(x.split())) >= 40), "")
        if len(body) >= DESCRIPTION[0] or (body and len(p.h1) and p.h1[0].strip()):
            cand = _cut(body, 155)
            if len(cand) >= DESCRIPTION[0]:
                out["description"] = cand
    n = len(p.title)
    h1 = " ".join((p.h1[0] if p.h1 else "").split())
    if n > TITLE[1]:
        out["title"] = _cut(p.title, TITLE[1])
    elif n < TITLE[0]:
        base = h1 if len(h1) > n else p.title
        if site_name and base and site_name.lower() not in base.lower() and len(base) + len(site_name) + 3 <= TITLE[1]:
            base = f"{base} | {site_name}"
        if base and base != p.title and len(base) >= TITLE[0]:
            out["title"] = base[: TITLE[1]]
    return out


def issue(fid: str, level: str, title: str, fix: str = "", **vars: object) -> dict:
    """``title`` and ``fix`` are texts the dashboard translates; ``vars`` fill their {placeholders}."""
    return {"id": fid, "level": level, "title": title, "fix": fix, "vars": vars}


def check_page(url: str, p: SeoPage, noindex_ok: bool = False) -> list[dict]:
    out = []
    n = len(p.title)
    if not p.title:
        out.append(issue("title-missing", "error", "No title", "Give each page a short, unique <title>."))
    elif n < TITLE[0]:
        out.append(issue("title-short", "warn", "Title is short ({n} characters)", "Aim for {lo} to {hi} characters.", n=n, lo=TITLE[0], hi=TITLE[1]))
    elif n > TITLE[1]:
        out.append(issue("title-long", "warn", "Title is long ({n} characters)", "Search results cut it after about {hi} characters.", n=n, hi=TITLE[1]))
    desc = p.meta.get("description", "").strip()
    if not desc:
        out.append(issue("description-missing", "error", "No meta description", 'Add <meta name="description" content="…"> in 50 to 160 characters.'))
    elif not DESCRIPTION[0] <= len(desc) <= DESCRIPTION[1]:
        out.append(issue("description-length", "warn", "Description is {n} characters", "Aim for {lo} to {hi} characters.", n=len(desc), lo=DESCRIPTION[0], hi=DESCRIPTION[1]))
    if len(p.h1) == 0 or not any(h.strip() for h in p.h1):
        out.append(issue("h1-missing", "warn", "No main heading (h1)", "One h1 per page says what it is about."))
    elif len(p.h1) > 1:
        out.append(issue("h1-many", "info", "{n} main headings", "Keep a single h1 per page.", n=len(p.h1)))
    if not p.lang:
        out.append(issue("lang-missing", "warn", "No page language", 'Add lang="fr" (or your language) on <html>.'))
    if "viewport" not in p.meta:
        out.append(issue("viewport-missing", "warn", "No viewport", 'Add <meta name="viewport" content="width=device-width, initial-scale=1"> for phones.'))
    if p.no_alt:
        out.append(issue("img-alt", "warn", "{n} of {m} images without alt text", "Describe each image (alt=\"\" for decoration).", n=p.no_alt, m=p.images))
    if not p.canonical:
        out.append(issue("canonical-missing", "info", "No canonical address", 'Add <link rel="canonical" href="…"> to avoid duplicates.'))
    if "noindex" in p.meta.get("robots", "").lower() and not noindex_ok:
        out.append(issue("noindex", "error", "The page asks not to be indexed", "Remove noindex from the robots meta tag if it should appear in search."))
    if not (p.meta.get("og:title") and p.meta.get("og:image")):
        out.append(issue("og-missing", "info", "No social preview (Open Graph)", "og:title and og:image make shared links look right."))
    return out


def audit(domain: str, fetch: Callable[..., Response] = http_fetch, site_name: str = "") -> dict:
    host = domain.lower().strip().rstrip(".")
    home = fetch(f"https://{host}/")
    if home.status == 0:
        return {"domain": host, "score": 0, "reachable": False, "site": [issue("unreachable", "error", "The site could not be reached")], "pages": []}
    site: list[dict] = []
    robots = fetch(f"https://{host}/robots.txt")
    robots_text = robots.text if robots.status == 200 and "html" not in robots.header("content-type").lower() else ""
    if not robots_text:
        site.append(issue("robots-missing", "warn", "No robots.txt", "Turn on robots.txt below: it points search engines to your sitemap."))
    elif re.search(r"(?im)^\s*disallow:\s*/\s*$", robots_text) and re.search(r"(?im)^\s*user-agent:\s*\*", robots_text):
        site.append(issue("robots-blocks", "error", "robots.txt blocks the whole site", "Remove “Disallow: /” for User-agent: *."))
    sitemap = fetch(f"https://{host}/sitemap.xml")
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap.text) if sitemap.status == 200 else []
    if sitemap.status != 200 or not locs:
        site.append(issue("sitemap-missing", "warn", "No sitemap.xml", "Turn on the sitemap below: it lists every page for search engines."))
    elif robots_text and "sitemap:" not in robots_text.lower():
        site.append(issue("sitemap-unlisted", "info", "robots.txt does not mention the sitemap", "Add a “Sitemap: https://…/sitemap.xml” line."))
    plain = fetch(f"http://{host}/", follow=False)
    if plain.status not in (301, 308) and plain.status != 0:
        site.append(issue("http-redirect", "warn", "http:// does not redirect to https://", "Search engines should only see one version of the site."))
    todo = [home.url] + [u for u in locs if (urlsplit(u).hostname or "").lower() == host]
    seen, pages = set(), []
    paths: set[str] = {urlsplit(u).path or "/" for u in locs if (urlsplit(u).hostname or "").lower() == host}
    for url in todo:
        key = url.split("#")[0].rstrip("/")
        if key in seen or len(pages) >= MAX_PAGES:
            continue
        seen.add(key)
        resp = home if url == home.url else fetch(url.split("#")[0])
        if resp.status != 200 or "html" not in (resp.header("content-type") or "html").lower():
            if resp.status >= 400:
                pages.append({"url": url, "status": resp.status, "title": "", "issues": [issue("status", "error", "The page answers {n}", n=resp.status)]})
            continue
        p = read(resp.text)
        pages.append({"url": url, "status": 200, "title": p.title, "description": p.meta.get("description", ""),
                      "issues": check_page(url, p), "suggestions": suggest(p, site_name)})
        if resp is home:
            home_links = [urlsplit(urljoin(resp.url, l)) for l in p.links]
            paths.update(x.path for x in home_links if (x.hostname or "").lower() == host and x.path.startswith("/"))
    titles: dict[str, list[str]] = {}
    for pg in pages:
        if pg.get("title"):
            titles.setdefault(pg["title"], []).append(pg["url"])
    for title, urls in titles.items():
        if len(urls) > 1:
            for pg in pages:
                if pg["url"] in urls:
                    pg["issues"].append(issue("title-duplicate", "warn", "Same title as another page", "Each page needs its own title."))
    weight = {"error": 12, "warn": 5, "info": 1}
    all_issues = site + [i for pg in pages for i in pg["issues"]]
    per_page = max(1, len(pages))
    penalty = sum(weight[i["level"]] for i in site) + sum(weight[i["level"]] for pg in pages for i in pg["issues"]) / per_page
    return {"domain": host, "reachable": True, "score": max(0, round(100 - penalty)), "site": site, "pages": pages,
            "paths": sorted(paths)[:300], "has_robots": bool(robots_text), "has_sitemap": bool(locs),
            "counts": {lv: sum(1 for i in all_issues if i["level"] == lv) for lv in ("error", "warn", "info")}}
