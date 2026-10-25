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

from mas.config import Config
from mas.db import Database
from mas.host import Host
from mas.models import Service

# 1.2.3.4 - - [21/Oct/2026:20:14:25 +0200] "GET /path HTTP/2.0" 200 568 "ref" "agent"
LINE_RE = re.compile(r'^(\S+) \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):[^\]]*\] "(\S+) ([^" ]*)[^"]*" (\d{3}) \S+'
                     r'(?: "[^"]*" "([^"]*)")?')
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


def tally(lines) -> dict[str, dict]:
    """{day: {requests, pageviews, visitors (set of hashes), pages (Counter)}} from access log lines."""
    days: dict[str, dict] = defaultdict(lambda: {"requests": 0, "pageviews": 0, "visitors": set(),
                                                 "pages": Counter()})
    for line in lines:
        m = LINE_RE.match(line)
        if not m or m.group(3) not in MONTHS:
            continue
        ip, dd, mon, yyyy, method, target, status, agent = m.groups()
        day = f"{yyyy}-{MONTHS[mon]:02d}-{dd}"
        d = days[day]
        d["requests"] += 1
        path = unquote(urlsplit(target).path or "/")[:200]
        if (method != "GET" or not status.startswith(("2", "3")) or BOT_RE.search(agent or "")
                or FILE_RE.search(path) or PROBE_RE.search(path)):
            continue
        d["pageviews"] += 1
        d["visitors"].add(hashlib.blake2b(f"{day}|{ip}|{agent}".encode(), digest_size=8).digest())
        d["pages"][path] += 1
    return days


def count(lines) -> dict[str, dict]:
    """{day: {requests, pageviews, visitors, pages}} from access log lines."""
    return {day: {"day": day, "requests": d["requests"], "pageviews": d["pageviews"], "visitors": len(d["visitors"]),
                  "pages": dict(d["pages"].most_common(TOP_PAGES))} for day, d in tally(lines).items()}


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
    def __init__(self, host: Host, config: Config, db: Database, find_service=lambda service_id: None) -> None:
        self.host, self.config, self.db = host, config, db
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

    def _recount(self, app: dict, files: list[str]) -> None:
        budget = [MAX_BYTES]
        days = tally(for_hosts((line for path in files for line in _lines(self.host, path, budget)), app.get("hosts")))
        self._read_to = {}
        if days:
            self.db.save_site_stats(app["id"], [
                {"day": day, "requests": d["requests"], "pageviews": d["pageviews"], "visitors": len(d["visitors"]),
                 "pages": dict(d["pages"].most_common(TOP_PAGES))} for day, d in days.items()])
        recent = (date.today() - timedelta(days=1)).isoformat()
        self.db.keep_site_visitors(app["id"], recent, {day: d["visitors"] for day, d in days.items() if day >= recent})

    def _add(self, app: dict, chunks: list[tuple[str, int]]) -> None:
        self._read_to = {}
        for path, offset in chunks:
            data = self.host.read_from(path, offset, CHUNK_BYTES)
            cut = data.rfind(b"\n") + 1  # a line still being written stays for next time
            self._read_to[path] = offset + cut
            for day, d in tally(for_hosts(data[:cut].decode("utf-8", "replace").splitlines(), app.get("hosts"))).items():
                self.db.add_site_counts(app["id"], day, d["requests"], d["pageviews"], d["visitors"], dict(d["pages"]),
                                        TOP_PAGES)
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
        series, pages = [], Counter()
        for i in range(days):
            day = (first + timedelta(days=i)).isoformat()
            row = stored.get(day)
            series.append({"day": day, "requests": row["requests"] if row else 0,
                           "pageviews": row["pageviews"] if row else 0, "visitors": row["visitors"] if row else 0})
            if row and row["pages"]:
                pages.update(json.loads(row["pages"]))
        total = lambda key: sum(d[key] for d in series)  # noqa: E731
        return {"days": series, "since": first.isoformat(), "tracked_since": min(stored) if stored else None,
                "totals": {"requests": total("requests"), "pageviews": total("pageviews"),
                           "visitors": total("visitors")},
                "top_pages": [{"path": p, "views": v} for p, v in pages.most_common(10)],
                "updated_at": datetime.now().isoformat(timespec="seconds")}
