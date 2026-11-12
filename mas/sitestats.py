"""Visits of a site, counted from its nginx access logs (combined format).

Mas writes one access log per app (``/var/log/nginx/mas-app-<id>.access.log``, rotated by logrotate:
``.1``, ``.2.gz``…). Every few minutes the days still in those files are recounted and kept in the
``site_stats`` table, so the history outlives the rotation. Only totals are stored: no IP address,
no user agent. A visitor is an IP + user agent pair, hashed in memory for the day being counted.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import unquote, urlsplit

from mas.cdn import REALIP_SNIPPET, is_cdn
from mas.config import Config
from mas.db import Database
from mas.host import Host
from mas.models import Service

# 1.2.3.4 - - [21/Oct/2026:20:14:25 +0200] "GET /path HTTP/2.0" 200 568 "ref" "agent"
LINE_RE = re.compile(r'^(\S+) \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):[^\]]*\] "(\S+) ([^" ]*)[^"]*" (\d{3}) \S+'
                     r'(?: "([^"]*)" "([^"]*)")?')
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                       "Dec"), 1)}
BOT_RE = re.compile(r"bot|crawl|spider|slurp|curl|wget|python|go-http|java/|httpclient|okhttp|scrapy|headless|"
                    r"monitor|uptime|preview|facebookexternalhit|scan|zgrab|masscan|nmap|nikto|^-?$", re.I)
FILE_RE = re.compile(r"\.(?:css|js|mjs|map|json|xml|txt|ico|png|jpe?g|gif|webp|avif|svg|woff2?|ttf|otf|eot|mp4|"
                     r"webm|mp3|pdf|zip|gz)$", re.I)
PROBE_RE = re.compile(r"(?:^|/)\.|wp-|\.php|/cgi-bin/|/vendor/|/admin", re.I)  # scanners looking for holes
REFRESH_SECONDS = 60
CHUNK_BYTES = 50 * 1024 * 1024  # new lines read per refresh at most (the rest comes next time)
MAX_BYTES = 200 * 1024 * 1024  # read at most this much log per refresh
TOP_PAGES = 50
HOSTS_LOG = "/var/log/nginx/mas-hosts.access.log"  # "<host> <combined line>" of the sites without a log (mas_hosts)


def access_logs(host: Host, config: Config, app: dict, service: Service | None = None) -> list[str]:
    """The access log of the app and its rotated copies, oldest first."""
    if app.get("mode") == "managed" or not service:
        bases = [f"/var/log/nginx/mas-app-{app['id']}.access.log"]
    elif str(app["id"]).startswith("svc:"):  # a site of the server: its own log, else its lines of the shared one
        bases = own_access_logs(service) or [HOSTS_LOG]
    else:  # an adopted site keeps the logs of its own vhost
        bases = [p for p in service.log_paths if "access" in p.rsplit("/", 1)[-1]]
    files: list[str] = []
    for base in bases:
        rotated = [p for p in host.glob(f"{base}.*") if re.fullmatch(r"\.\d+(?:\.gz)?", p[len(base):])]
        rotated.sort(key=lambda p: int(p[len(base) + 1:].split(".")[0]), reverse=True)
        files += rotated + ([base] if host.exists(base) else [])
    return files


def _lines(host: Host, path: str, budget: list[int]):
    data = host.read_bytes(path, max_bytes=min(budget[0], MAX_BYTES)) or b""
    budget[0] -= len(data)
    if path.endswith(".gz"):
        try:
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_BYTES)
        except (OSError, EOFError):
            return
    yield from data.decode("utf-8", "replace").splitlines()


def for_hosts(lines, hosts: list[str] | None):
    """Lines of the shared mas_hosts log that are for these sites, without the leading host."""
    if not hosts:
        yield from lines
        return
    wanted = {h.lower() for h in hosts}
    for line in lines:
        name, _, rest = line.partition(" ")
        if name.lower() in wanted:
            yield rest


SEARCH = {"google": "google", "bing": "bing", "duckduckgo": "duckduckgo", "ecosia": "ecosia", "qwant": "qwant",
          "yahoo": "yahoo", "baidu": "baidu", "yandex": "yandex", "brave": "brave"}
SOCIAL = {"facebook": "facebook", "fb.com": "facebook", "instagram": "instagram", "t.co": "x", "twitter": "x", "x.com": "x",
          "linkedin": "linkedin", "lnkd.in": "linkedin", "youtube": "youtube", "tiktok": "tiktok", "pinterest": "pinterest",
          "reddit": "reddit", "whatsapp": "whatsapp", "snapchat": "snapchat"}


def classify_source(referrer: str, query: str, own_hosts: set[str]) -> str:
    """Where a visitor came from: "utm|newsletter", "search|google", "social|instagram", "ref|example.com", "direct|"."""
    for part in query.split("&"):
        if part.startswith("utm_source=") and len(part) > 11:
            return "utm|" + unquote(part[11:]).lower()[:40]
    host = (urlsplit(referrer).hostname or "").lower().removeprefix("www.") if referrer and referrer != "-" else ""
    if not host or host in own_hosts:
        return "direct|"
    for key, name in SEARCH.items():
        if key in host:
            return f"search|{name}"
    for key, name in SOCIAL.items():
        if host == key or key in host and key != "t.co" and key != "x.com":
            return f"social|{name}"
    return "ref|" + host[:60]


def classify_device(agent: str) -> tuple[str, str]:
    """(device, browser) from a user agent: mobile / tablet / desktop and a browser family."""
    a = agent or ""
    if re.search(r"iPad|Tablet|Android(?!.*Mobile)", a):
        device = "tablet"
    elif re.search(r"Mobi|iPhone|Android", a):
        device = "mobile"
    else:
        device = "desktop"
    for rx, name in ((r"Edg(e|A|iOS)?/", "Edge"), (r"OPR/|Opera", "Opera"), (r"SamsungBrowser", "Samsung"),
                     (r"Firefox/|FxiOS", "Firefox"), (r"Chrome/|CriOS", "Chrome"), (r"Safari/", "Safari")):
        if re.search(rx, a):
            return device, name
    return device, "Other"


def matches_goal_path(path: str, goal_paths: list[str]) -> str | None:
    """The configured goal path a visited path counts for ("/merci" exact, "/blog/*" prefix)."""
    for g in goal_paths:
        if (g.endswith("*") and path.startswith(g[:-1])) or path.rstrip("/") == g.rstrip("/") or (g == "/" and path == "/"):
            return g
    return None


def tally(lines, geo=None, own_hosts: set[str] | None = None, goal_paths: list[str] | None = None) -> dict[str, dict]:
    """{day: {requests, pageviews, visitors (set of hashes), pages (Counter), geo {hash: country}}}.

    ``geo`` (mas.geo.Geo) gives the country of a visitor; the address itself is never kept. For each new
    visitor the first page they open also gives their source, device and browser; ``reached`` lists, per
    goal path, the visitors who opened it."""
    own = own_hosts or set()
    days: dict[str, dict] = defaultdict(lambda: {"requests": 0, "pageviews": 0, "visitors": set(),
                                                 "pages": Counter(), "geo": {}, "traits": {}, "reached": defaultdict(set)})
    for line in lines:
        m = LINE_RE.match(line)
        if not m or m.group(3) not in MONTHS:
            continue
        ip, dd, mon, yyyy, method, target, status, referrer, agent = m.groups()
        day = f"{yyyy}-{MONTHS[mon]:02d}-{dd}"
        d = days[day]
        d["requests"] += 1
        path = unquote(urlsplit(target).path or "/")[:200]
        if (method != "GET" or not status.startswith(("2", "3")) or BOT_RE.search(agent or "")
                or FILE_RE.search(path) or PROBE_RE.search(path)):
            continue
        d["pageviews"] += 1
        who = hashlib.blake2b(f"{day}|{ip}|{agent}".encode(), digest_size=8).digest()
        if who not in d["visitors"]:  # the first page of a visitor tells where they came from
            device, browser = classify_device(agent or "")
            d["traits"][who] = {"source": classify_source(referrer or "", urlsplit(target).query, own),
                                "device": device, "browser": browser}
        d["visitors"].add(who)
        if goal_paths and (hit := matches_goal_path(path, goal_paths)):
            d["reached"][hit].add(who)
        if geo is not None and who not in d["geo"]:
            code = "_cdn" if is_cdn(ip) else geo.country(ip)  # a CDN edge says nothing about the visitor
            if code:
                d["geo"][who] = code
        d["pages"][path] += 1
    return days


def trait_counts(d: dict) -> tuple[dict, dict]:
    """(sources, devices) counted per visitor of a day from the traits read in the logs."""
    sources = Counter(t["source"] for t in d["traits"].values())
    devices = Counter(f"device:{t['device']}" for t in d["traits"].values()) + \
        Counter(f"browser:{t['browser']}" for t in d["traits"].values())
    return dict(sources), dict(devices)


def count(lines, geo=None, own_hosts=None, goal_paths=None) -> dict[str, dict]:
    """{day: {requests, pageviews, visitors, pages, countries, sources, devices}} from access log lines."""
    out = {}
    for day, d in tally(lines, geo, own_hosts, goal_paths).items():
        sources, devices = trait_counts(d)
        out[day] = {"day": day, "requests": d["requests"], "pageviews": d["pageviews"], "visitors": len(d["visitors"]),
                    "pages": dict(d["pages"].most_common(TOP_PAGES)), "countries": dict(Counter(d["geo"].values())),
                    "sources": sources, "devices": devices, "reached": {k: len(v) for k, v in d["reached"].items()}}
    return out


def own_access_logs(service: Service) -> list[str]:
    """Access logs of this site only: the default /var/log/nginx/access.log is shared by every site
    without a log of its own (the combined format does not say which site a line is for)."""
    return [p for p in service.log_paths if "access" in p.rsplit("/", 1)[-1]
            and p.rsplit("/", 1)[-1] not in ("access.log", "other_vhosts_access.log")]


def service_target(service: Service, apps: list[dict], apps_dir: str) -> dict | None:
    """What to count for a service of the scan: its Mas app, or the service itself when its nginx site
    has an access log of its own (``own_log``); None when it has no domain."""
    from mas.runtime import app_for  # runtime imports the collectors: kept out of the module import
    linked = app_for(service, apps, apps_dir)
    if linked:
        return next((a for a in apps if a["id"] == linked["id"]), None)
    if not service.domains:
        return None
    own = bool(own_access_logs(service))
    return {"id": f"svc:{service.id}", "mode": "adopted", "service_id": service.id, "domain": service.domains[0],
            "own_log": own, "hosts": None if own else list(service.domains), "_service": service}


class SiteStats:
    def __init__(self, host: Host, config: Config, db: Database, find_service=lambda service_id: None,
                 geo=None) -> None:
        self.host, self.config, self.db = host, config, db
        self.geo = geo  # mas.geo.Geo: countries for the traffic map (optional)
        self.find_service = find_service
        self._done: dict[str, float] = {}
        self._read_to: dict[str, int] = {}  # where the last reading stopped, per file

    def refresh(self, app: dict, force: bool = False) -> None:
        """Count what the access log got since the last time (at most every minute per app).

        The first time — or when the position is lost — every day still in the logs is recounted.
        Then only the new bytes are read: the position (inode, offset) of the log is kept, and a
        rotation (the log became .1) is followed. Today's and yesterday's visitors are kept as hashes
        so that a visitor is counted once per day."""
        if not force and time.monotonic() - self._done.get(app["id"], -1e9) < REFRESH_SECONDS:
            return
        self._done[app["id"]] = time.monotonic()
        service = app.get("_service") or (self.find_service(app["service_id"]) if app.get("service_id") else None)
        files = access_logs(self.host, self.config, app, service)
        if not files or files[-1].endswith(".gz") or re.search(r"\.\d+$", files[-1]):
            return  # no current log
        base = files[-1]
        key = f"sitestats:{app['id']}"
        try:
            position = json.loads(self.db.get_setting(key) or "null")
        except ValueError:
            position = None
        now_id = self.host.file_id(base)
        if now_id is None:
            return
        chunks: list[tuple[str, int]] = []  # (file, offset) to read
        if position and position.get("file") == base:
            if position["inode"] == now_id[0] and position["offset"] <= now_id[1]:
                chunks = [(base, position["offset"])]
            else:  # rotated: finish the old file (now .1), then the new one from the start
                rotated = next((f for f in files[:-1] if not f.endswith(".gz")
                                and (self.host.file_id(f) or (0,))[0] == position["inode"]), None)
                chunks = ([(rotated, position["offset"])] if rotated else []) + [(base, 0)]
                if rotated is None:
                    position = None  # lost the thread: recount everything
        if not position:
            self._recount(app, files)
        else:
            self._add(app, chunks)
        end = self.host.file_id(base) or now_id
        self.db.set_setting(key, json.dumps({"file": base, "inode": end[0], "offset": self._read_to.get(base, end[1])}))

    def goal_paths(self, app_id: str) -> list[str]:
        try:
            return [p for p in json.loads(self.db.get_setting(f"goals:{app_id}") or "{}").get("paths", []) if isinstance(p, str)]
        except ValueError:
            return []

    @staticmethod
    def own_hosts(app: dict) -> set[str]:
        names = [app.get("domain")]
        try:
            names += json.loads(app["aliases"]) if isinstance(app.get("aliases"), str) else (app.get("aliases") or [])
        except ValueError:
            pass
        return {n.lower().removeprefix("www.") for n in names if isinstance(n, str)}

    def _recount(self, app: dict, files: list[str]) -> None:
        budget = [MAX_BYTES]
        paths = self.goal_paths(app["id"])
        days = tally(for_hosts((line for path in files for line in _lines(self.host, path, budget)), app.get("hosts")),
                     self.geo, self.own_hosts(app), paths)
        self._read_to = {}
        if days:
            rows = []
            for day, d in days.items():
                sources, devices = trait_counts(d)
                rows.append({"day": day, "requests": d["requests"], "pageviews": d["pageviews"], "visitors": len(d["visitors"]),
                             "pages": dict(d["pages"].most_common(TOP_PAGES)), "countries": dict(Counter(d["geo"].values())),
                             "sources": sources, "devices": devices})
            self.db.save_site_stats(app["id"], rows)
        recent = (date.today() - timedelta(days=1)).isoformat()
        for day, d in days.items():  # page goals: only days whose visitors are still remembered, to never count twice
            if day >= recent:
                for goal, hashes in d["reached"].items():
                    for h in hashes:
                        self.db.add_goal(app["id"], day, f"page:{goal}", h, 30)
        self.db.keep_site_visitors(app["id"], recent, {day: d["visitors"] for day, d in days.items() if day >= recent})

    def _add(self, app: dict, chunks: list[tuple[str, int]]) -> None:
        self._read_to = {}
        for path, offset in chunks:
            data = self.host.read_from(path, offset, CHUNK_BYTES)
            cut = data.rfind(b"\n") + 1  # a line still being written stays for next time
            self._read_to[path] = offset + cut
            lines = for_hosts(data[:cut].decode("utf-8", "replace").splitlines(), app.get("hosts"))
            for day, d in tally(lines, self.geo, self.own_hosts(app), self.goal_paths(app["id"])).items():
                traits = {h: {**t, **({"country": d["geo"][h]} if h in d["geo"] else {})} for h, t in d["traits"].items()}
                self.db.add_site_counts(app["id"], day, d["requests"], d["pageviews"], d["visitors"], dict(d["pages"]),
                                        TOP_PAGES, traits)
                for goal, hashes in d["reached"].items():
                    for h in hashes:
                        self.db.add_goal(app["id"], day, f"page:{goal}", h, 30)
        self.db.keep_site_visitors(app["id"], (date.today() - timedelta(days=1)).isoformat())

    def refresh_all(self, services: list[Service] = ()) -> None:
        apps = self.db.list_apps()
        for app in apps:
            if app.get("domain") and app.get("mode") in ("managed", "adopted", "stack"):
                self.refresh(app)
        for svc in services:  # sites of the server that Mas does not deploy, with an access log of their own
            target = service_target(svc, apps, self.config.apps_dir)
            if target and str(target["id"]).startswith("svc:"):
                self.refresh(target)

    def report(self, app: dict, days: int = 30, today: date | None = None) -> dict:
        self.refresh(app)
        today = today or date.today()
        first = today - timedelta(days=days - 1)
        stored = {r["day"]: r for r in self.db.site_stats(app["id"], first.isoformat())}
        goal_rows = self.db.site_goals(app["id"], first.isoformat())
        converted = {r["day"]: r["visitors"] for r in goal_rows if r["goal"] == "_any"}
        series, pages, countries, sources, devices = [], Counter(), Counter(), Counter(), Counter()
        for i in range(days):
            day = (first + timedelta(days=i)).isoformat()
            row = stored.get(day)
            series.append({"day": day, "requests": row["requests"] if row else 0,
                           "pageviews": row["pageviews"] if row else 0, "visitors": row["visitors"] if row else 0,
                           "converted": converted.get(day, 0)})
            if row and row["pages"]:
                pages.update(json.loads(row["pages"]))
            if row and row["countries"]:
                countries.update(json.loads(row["countries"]))
            if row and row["sources"]:
                sources.update(json.loads(row["sources"]))
            if row and row["devices"]:
                devices.update(json.loads(row["devices"]))
        total = lambda key: sum(d[key] for d in series)  # noqa: E731
        visitors = total("visitors")
        per_goal: dict[str, dict] = {}
        for r in goal_rows:
            if r["goal"] != "_any":
                g = per_goal.setdefault(r["goal"], {"goal": r["goal"], "hits": 0, "visitors": 0})
                g["hits"] += r["hits"]
                g["visitors"] += r["visitors"]
        rate = lambda n: round(100 * n / visitors, 2) if visitors else 0.0  # noqa: E731
        goals = sorted(({**g, "rate": rate(g["visitors"])} for g in per_goal.values()), key=lambda g: -g["visitors"])
        reached = sum(converted.values())
        try:
            wanted = [x for x in json.loads(self.db.get_setting(f"goals:{app['id']}") or "{}").get("funnel", []) if isinstance(x, str)]
        except ValueError:
            wanted = []
        funnel = [{"step": "visitors", "visitors": visitors}] + [
            {"step": g, "visitors": per_goal.get(g, {}).get("visitors", 0)} for g in wanted] if wanted else []
        return {"days": series, "goals": goals, "funnel": funnel,
                "goal_paths": self.goal_paths(app["id"]), "funnel_steps": wanted, "conversion": {"visitors": reached, "rate": rate(reached)},
                "since": first.isoformat(), "tracked_since": min(stored) if stored else None,
                "totals": {"requests": total("requests"), "pageviews": total("pageviews"),
                           "visitors": total("visitors")},
                "top_pages": [{"path": p, "views": v} for p, v in pages.most_common(10)],
                "countries": [{"code": c, "visitors": v} for c, v in countries.most_common(61) if c != "_cdn"][:60],
                "cdn": ({"share": round(countries["_cdn"] / max(1, sum(countries.values())), 2), "snippet": REALIP_SNIPPET}
                        if countries.get("_cdn") else None),
                "geo": bool(self.geo and self.geo.available()),
                "sources": [{"kind": k.split("|")[0], "name": k.split("|", 1)[1], "visitors": v}
                            for k, v in sources.most_common(20)],
                "devices": {k[7:]: v for k, v in devices.items() if k.startswith("device:")},
                "browsers": [{"name": k[8:], "visitors": v} for k, v in devices.most_common() if k.startswith("browser:")][:8],
                "updated_at": datetime.now().isoformat(timespec="seconds")}
