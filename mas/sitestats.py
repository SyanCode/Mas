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
REFRESH_SECONDS = 600
MAX_BYTES = 200 * 1024 * 1024  # read at most this much log per refresh
TOP_PAGES = 50


def access_logs(host: Host, config: Config, app: dict, service: Service | None = None) -> list[str]:
    """The access log of the app and its rotated copies, oldest first."""
    if app.get("mode") == "managed" or not service:
        bases = [f"/var/log/nginx/mas-app-{app['id']}.access.log"]
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


def count(lines) -> dict[str, dict]:
    """{day: {requests, pageviews, visitors, pages}} from access log lines."""
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
    return {day: {"day": day, "requests": d["requests"], "pageviews": d["pageviews"], "visitors": len(d["visitors"]),
                  "pages": dict(d["pages"].most_common(TOP_PAGES))} for day, d in days.items()}


class SiteStats:
    def __init__(self, host: Host, config: Config, db: Database, find_service=lambda service_id: None) -> None:
        self.host, self.config, self.db = host, config, db
        self.find_service = find_service
        self._done: dict[str, float] = {}

    def refresh(self, app: dict, force: bool = False) -> None:
        """Recount the days still in the logs (at most every 10 minutes per app)."""
        if not force and time.monotonic() - self._done.get(app["id"], -1e9) < REFRESH_SECONDS:
            return
        self._done[app["id"]] = time.monotonic()
        service = self.find_service(app["service_id"]) if app.get("service_id") else None
        budget = [MAX_BYTES]
        lines = (line for path in access_logs(self.host, self.config, app, service)
                 for line in _lines(self.host, path, budget))
        days = count(lines)
        if days:
            self.db.save_site_stats(app["id"], list(days.values()))

    def refresh_all(self) -> None:
        for app in self.db.list_apps():
            if app.get("domain") and app.get("mode") in ("managed", "adopted"):
                self.refresh(app)

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
