"""A look at how the live site presents itself to search engines: titles, descriptions, headings, images,
social previews, robots.txt and the sitemap, structured data, broken and orphan pages, thin or JavaScript-only pages.
Same crawl rules as the compliance audit (a few pages, HTTPS only). ``CHECKLIST`` maps the result on the 22 points of a
classic SEO checklist, so the robot (and the person) see what is checked, what fails and what needs a human."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from mas.compliance import Response, http_fetch

MAX_PAGES = 8
MAX_LINK_CHECKS = 20  # internal links fetched to find broken ones, on top of the crawled pages
TITLE = (15, 60)
DESCRIPTION = (50, 160)
THIN_WORDS = 150
SKIP = ("script", "style", "nav", "header", "footer", "noscript", "template")
HEADING = re.compile(r"^h([1-6])$")


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
        self.headings: list[int] = []  # levels in document order
        self.h2 = 0
        self.words = 0
        self.schema_types: set[str] = set()
        self.no_size = 0  # images without width and height: the layout jumps while they load
        self.blocking = 0  # scripts in <head> without async / defer / type=module
        self.app_root = False  # an empty <div id="root|app|__next"> mounted by JavaScript
        self._in = ""
        self._skip = 0
        self._head = False
        self._ld: list[str] | None = None
        self._lds: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        level = HEADING.match(tag)
        if level:
            self.headings.append(int(level.group(1)))
            self.h2 += tag == "h2"
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "head":
            self._head = True
        elif tag == "body":
            self._head = False
        elif tag == "title" and not self.title:
            self._in = "title"
        elif tag == "h1":
            self._in = "h1"
            self.h1.append("")
        elif tag == "script":
            kind = a.get("type", "").lower()
            if kind == "application/ld+json":
                self._ld = []
            elif self._head and a.get("src") and "async" not in a and "defer" not in a and kind != "module":
                self.blocking += 1
        elif tag == "div" and a.get("id", "").lower() in ("root", "app", "__next", "__nuxt", "svelte"):
            self.app_root = True
        if a.get("itemtype"):
            self.schema_types.add(a["itemtype"].rstrip("/").rsplit("/", 1)[-1])
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
            if not (a.get("width") and a.get("height")) and "aspect-ratio" not in a.get("style", ""):
                self.no_size += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag in SKIP:
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._in = "p"
            self.paragraphs.append("")

    def handle_endtag(self, tag):
        if tag in ("title", "h1", "p"):
            self._in = ""
        if tag == "head":
            self._head = False
        if tag == "script" and self._ld is not None:
            self._lds.append("".join(self._ld))
            self._ld = None
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
            return
        if not self._skip and not self._head and self._in != "title":
            self.words += len(re.findall(r"\w{2,}", data))
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
    for raw in page._lds:
        try:
            _types(json.loads(raw), page.schema_types)
        except ValueError:
            page.schema_types.add("invalid")
    return page


def _types(node: object, out: set[str], depth: int = 0) -> None:
    """Every @type of a JSON-LD document (graphs and nested items included)."""
    if depth > 6:
        return
    if isinstance(node, list):
        for x in node[:50]:
            _types(x, out, depth + 1)
    elif isinstance(node, dict):
        t = node.get("@type")
        for x in (t if isinstance(t, list) else [t]):
            if isinstance(x, str):
                out.add(x)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _types(v, out, depth + 1)


def unclean_url(url: str) -> str:
    """Why an address is not a clean, readable one ('' when it is)."""
    parts = urlsplit(url)
    path = parts.path
    if parts.query:
        return "query"
    if re.search(r"[A-Z]", path):
        return "uppercase"
    if "_" in path or "%20" in path or " " in path:
        return "separator"
    if len(path) > 100:
        return "long"
    return ""


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
    host = (urlsplit(url).hostname or "").lower()
    if p.canonical:
        target = (urlsplit(urljoin(url, p.canonical)).hostname or "").lower()
        if target and host and target != host:
            out.append(issue("canonical-elsewhere", "warn", "The canonical address points to another site ({host})",
                             "Point the canonical link to this page's own address.", host=target))
    shell = p.words < 20 and p.app_root
    if shell:
        out.append(issue("js-shell", "warn", "The page shows no text without JavaScript",
                         "Render the content on the server or prerender it (static export, SSR) so search engines read it."))
    elif p.words < THIN_WORDS:
        out.append(issue("thin-content", "info", "Thin page ({n} words)",
                         "Pages that rank answer the question fully: add useful, original text (or merge the page).", n=p.words))
    if p.words >= 300 and not p.h2:
        out.append(issue("h2-missing", "info", "Long page without subheadings (h2)", "Cut the text into sections with h2 headings."))
    last = 0
    for lv in p.headings:
        if last and lv > last + 1:
            out.append(issue("heading-skip", "info", "Heading levels skip (h{a} then h{b})",
                             "Keep the order h1 → h2 → h3 without jumping a level.", a=last, b=lv))
            break
        last = lv
    if not p.schema_types:
        out.append(issue("schema-missing", "info", "No structured data (schema.org)",
                         "Add JSON-LD: Organization or LocalBusiness on the home page, Article, Product or FAQPage where it fits."))
    elif "invalid" in p.schema_types:
        out.append(issue("schema-invalid", "warn", "Structured data that is not valid JSON",
                         "Fix the JSON-LD block: search engines ignore it as it is."))
    if len([x for x in urlsplit(url).path.split("/") if x]) >= 2 and "BreadcrumbList" not in p.schema_types:
        out.append(issue("breadcrumbs-missing", "info", "No breadcrumbs on a deep page",
                         "Show a breadcrumb trail and describe it with BreadcrumbList structured data."))
    why = unclean_url(url)
    if why:
        out.append(issue("url-unclean", "info", "The address is not clean ({why})",
                         "Use short lowercase addresses with hyphens, without parameters.", why=why))
    if p.no_size:
        out.append(issue("img-size", "info", "{n} images without width and height",
                         "Give images their size (or an aspect ratio): the page stops jumping while they load (CLS).", n=p.no_size))
    if p.blocking:
        out.append(issue("render-blocking", "info", "{n} scripts block the first display",
                         "Load scripts with defer (or async) or at the end of the page (LCP).", n=p.blocking))
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
    status_of: dict[str, int] = {}  # path → status, for the pages already fetched
    links_of: dict[str, set[str]] = {}  # page url → internal paths it links to
    parsed: dict[str, SeoPage] = {}
    paths: set[str] = {urlsplit(u).path or "/" for u in locs if (urlsplit(u).hostname or "").lower() == host}
    for url in todo:
        key = url.split("#")[0].rstrip("/")
        if key in seen or len(pages) >= MAX_PAGES:
            continue
        seen.add(key)
        resp = home if url == home.url else fetch(url.split("#")[0])
        status_of[urlsplit(url).path or "/"] = resp.status
        if resp.status != 200 or "html" not in (resp.header("content-type") or "html").lower():
            if resp.status >= 400:
                pages.append({"url": url, "status": resp.status, "title": "", "issues": [issue("status", "error", "The page answers {n}", n=resp.status)]})
            continue
        p = read(resp.text)
        parsed[url] = p
        internal = [urlsplit(urljoin(resp.url, l)) for l in p.links if not l.startswith(("mailto:", "tel:", "javascript:", "#"))]
        links_of[url] = {x.path or "/" for x in internal if (x.hostname or "").lower() == host}
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
    _duplicates(pages, "description", "description-duplicate", "warn", "Same description as another page",
                "Write a description for each page: what it offers, in its own words.")
    for pg in pages:
        p = parsed.get(pg["url"])
        pg["h1"] = " ".join((p.h1[0] if p and p.h1 else "").split()).lower()
    _duplicates(pages, "h1", "h1-duplicate", "info", "Same main heading as another page",
                "Two pages about the same thing compete in search (cannibalization): merge them or give each its own topic.")
    for pg in pages:
        pg.pop("h1", None)
    # broken internal links: the pages already fetched, then up to MAX_LINK_CHECKS more
    checks = 0
    for pg in pages:
        broken = []
        for path in sorted(links_of.get(pg["url"], ())):
            if path not in status_of and checks < MAX_LINK_CHECKS:
                checks += 1
                status_of[path] = fetch(f"https://{host}{path}").status
            if status_of.get(path, 200) >= 400:
                broken.append(path)
        if broken:
            pg["issues"].append(issue("links-broken", "error", "{n} links lead to a missing page",
                                      "Fix or remove these links, or redirect the old address (301).", n=len(broken)) | {"links": broken[:10]})
    linked = set().union(*links_of.values()) if links_of else set()
    orphans = sorted({urlsplit(u).path or "/" for u in locs if (urlsplit(u).hostname or "").lower() == host} - linked - {"/"})
    if orphans and len(links_of) > 1:
        site.append(issue("orphans", "info", "{n} pages of the sitemap are linked from no page read",
                          "Link every page from at least one other page (menu, footer, related content).", n=len(orphans)) | {"pages": orphans[:15]})
    weight = {"error": 12, "warn": 5, "info": 1}
    all_issues = site + [i for pg in pages for i in pg["issues"]]
    per_page = max(1, len(pages))
    penalty = sum(weight[i["level"]] for i in site) + sum(weight[i["level"]] for pg in pages for i in pg["issues"]) / per_page
    return {"domain": host, "reachable": True, "score": max(0, round(100 - penalty)), "site": site, "pages": pages,
            "paths": sorted(paths)[:300], "has_robots": bool(robots_text), "has_sitemap": bool(locs),
            "counts": {lv: sum(1 for i in all_issues if i["level"] == lv) for lv in ("error", "warn", "info")},
            "checklist": checklist({i["id"] for i in all_issues})}


def _duplicates(pages: list[dict], key: str, fid: str, level: str, title: str, fix: str) -> None:
    seen: dict[str, list[dict]] = {}
    for pg in pages:
        value = (pg.get(key) or "").strip()
        if value:
            seen.setdefault(value, []).append(pg)
    for group in seen.values():
        if len(group) > 1:
            for pg in group:
                pg["issues"].append(issue(fid, level, title, fix))


# The 22 points of a classic SEO checklist. ``ids``: the audit findings that mean the point fails; no ids: a human (or
# the robot with the seo-checklist skill) has to judge it from the content and the search data.
CHECKLIST: list[dict] = [
    {"n": 1, "title": "Fix indexing issues", "ids": ["noindex", "robots-blocks", "robots-missing", "sitemap-missing", "sitemap-unlisted", "http-redirect", "status", "unreachable"]},
    {"n": 2, "title": "Add breadcrumbs", "ids": ["breadcrumbs-missing"]},
    {"n": 3, "title": "Add canonical tags", "ids": ["canonical-missing", "canonical-elsewhere"]},
    {"n": 4, "title": "Fix Core Web Vitals", "ids": ["img-size", "render-blocking"]},
    {"n": 5, "title": "Fix orphan pages", "ids": ["orphans"]},
    {"n": 6, "title": "Add schema markup", "ids": ["schema-missing", "schema-invalid"]},
    {"n": 7, "title": "Fix H1 / H2 structure", "ids": ["h1-missing", "h1-many", "h2-missing", "heading-skip"]},
    {"n": 8, "title": "Write original content", "ids": []},
    {"n": 9, "title": "Get quality backlinks", "ids": []},
    {"n": 10, "title": "Use clean, clear URLs", "ids": ["url-unclean"]},
    {"n": 11, "title": "Fix broken links & 404s", "ids": ["links-broken", "status"]},
    {"n": 12, "title": "Make JS crawlable", "ids": ["js-shell"]},
    {"n": 13, "title": "Fix cannibalization", "ids": ["title-duplicate", "h1-duplicate"]},
    {"n": 14, "title": "Go mobile-friendly", "ids": ["viewport-missing"]},
    {"n": 15, "title": "Author bio + E-E-A-T", "ids": []},
    {"n": 16, "title": "Titles: 50-60 chars", "ids": ["title-missing", "title-short", "title-long"]},
    {"n": 17, "title": "Write unique metas", "ids": ["description-missing", "description-length", "description-duplicate", "og-missing"]},
    {"n": 18, "title": "Fix thin pages", "ids": ["thin-content"]},
    {"n": 19, "title": "Optimize images + alt", "ids": ["img-alt", "img-size"]},
    {"n": 20, "title": "Match search intent", "ids": []},
    {"n": 21, "title": "High volume, low KD", "ids": []},
    {"n": 22, "title": "Internal linking", "ids": ["orphans", "links-broken"]},
]


def checklist(found: set[str]) -> list[dict]:
    """Each point of ``CHECKLIST``: ok, to fix (with the findings) or to judge by a person."""
    out = []
    for c in CHECKLIST:
        hits = sorted(found & set(c["ids"]))
        status = "manual" if not c["ids"] else ("fix" if hits else "ok")
        out.append({"n": c["n"], "title": c["title"], "status": status, **({"findings": hits} if hits else {})})
    return out
