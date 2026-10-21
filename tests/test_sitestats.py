"""Visits counted from nginx access logs."""

from __future__ import annotations

import gzip
from datetime import date

import server

from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost
from mas.sitestats import SiteStats, count

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"


def line(ip, day, path, status=200, agent=UA, method="GET"):
    return f'{ip} - - [{day}/Oct/2026:10:00:00 +0200] "{method} {path} HTTP/2.0" {status} 512 "-" "{agent}"'


def test_count_pages_people_and_requests():
    days = count([
        line("1.1.1.1", "20", "/"), line("1.1.1.1", "20", "/"), line("1.1.1.1", "20", "/assets/a.jpg"),
        line("2.2.2.2", "20", "/oeuvres?x=1"), line("3.3.3.3", "20", "/", agent="Googlebot/2.1"),
        line("4.4.4.4", "20", "//.env.save", status=404), line("4.4.4.4", "20", "/wp-login.php"),
        line("5.5.5.5", "20", "/contact", method="POST"), line("1.1.1.1", "21", "/"), "garbage",
    ])
    d20 = days["2026-10-20"]
    assert d20["requests"] == 8 and d20["pageviews"] == 3 and d20["visitors"] == 2
    assert d20["pages"] == {"/": 2, "/oeuvres": 1}
    assert days["2026-10-21"]["visitors"] == 1


def test_report_reads_rotated_logs_and_keeps_the_history(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_app("bb", name="bb", repo="acme/bb", branch="main", type="static", domain="bb.example.com")
    log = "/var/log/nginx/mas-app-bb.access.log"
    host.write_text(log, line("1.1.1.1", "21", "/") + "\n")
    host.write_text(f"{log}.1", line("1.1.1.1", "20", "/") + "\n" + line("2.2.2.2", "20", "/a") + "\n")
    real = host.real(f"{log}.2.gz")
    real.write_bytes(gzip.compress((line("9.9.9.9", "19", "/old") + "\n").encode()))
    stats = SiteStats(host, Config(), db)
    app = db.get_app("bb")
    r = stats.report(app, days=7, today=date(2026, 10, 21))
    by_day = {d["day"]: d for d in r["days"]}
    assert by_day["2026-10-19"]["visitors"] == 1 and by_day["2026-10-20"]["pageviews"] == 2
    assert r["totals"]["pageviews"] == 4 and r["top_pages"][0] == {"path": "/", "views": 2}
    assert len(r["days"]) == 7 and r["tracked_since"] == "2026-10-19"
    # rotated away: the days stay in the database
    host.remove(f"{log}.2.gz")
    stats.refresh(app, force=True)
    assert stats.report(app, days=7, today=date(2026, 10, 21))["totals"]["pageviews"] == 4
    assert "1.1.1.1" not in str(db.site_stats("bb", "2026-01-01"))  # no IP kept
