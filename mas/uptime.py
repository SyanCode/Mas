"""Uptime monitoring: Mas opens each site's address regularly, keeps the answers and the incidents.

A site is down after two failed checks in a row (one bad answer is often a network blip); it is back at
the first good answer. Incidents feed the ``uptime`` notification and the optional public status page.
Anything under 500 counts as up: a 404 page is a working server.
"""

from __future__ import annotations

import html
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from mas.compliance import Response, http_fetch
from mas.db import Database

log = logging.getLogger(__name__)

INTERVAL = 55  # seconds between two checks of a site
FAILS_FOR_DOWN = 2
KEEP_DAYS = 35


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Uptime:
    def __init__(self, db: Database, fetch=http_fetch, notifier=None) -> None:
        self.db, self.fetch, self.notifier = db, fetch, notifier
        self._pruned = 0.0

    def check(self, app: dict, now: float | None = None) -> dict:
        now = now or time.time()
        started = time.monotonic()
        resp: Response = self.fetch(f"https://{app['domain']}/", follow=True)
        ms = int((time.monotonic() - started) * 1000)
        ok = resp.status != 0 and resp.status < 500
        self.db.add_uptime_check(app["id"], _iso(now), ok, resp.status, ms)
        self._transition(app, ok, resp, now)
        return {"ok": ok, "status": resp.status, "ms": ms}

    def _transition(self, app: dict, ok: bool, resp: Response, now: float) -> None:
        open_incident = self.db.open_incident(app["id"])
        if ok and open_incident:
            self.db.close_incident(open_incident["id"], _iso(now))
            minutes = max(1, int((now - datetime.fromisoformat(open_incident["started"]).timestamp()) / 60))
            self._tell(app, "Back online", f"{app['domain']} answers again after about {minutes} minute(s).")
        elif not ok and not open_incident:
            recent = self.db.recent_uptime(app["id"], FAILS_FOR_DOWN)
            if len(recent) >= FAILS_FOR_DOWN and not any(r["ok"] for r in recent):
                reason = {"tls": "invalid HTTPS certificate", "network": "no answer"}.get(resp.error) or f"HTTP {resp.status}"
                self.db.open_incident_row(app["id"], recent[-1]["at"], reason)
                self._tell(app, "Site down", f"{app['domain']} does not answer properly ({reason}).")

    def _tell(self, app: dict, title: str, text: str) -> None:
        if self.notifier is not None:
            self.notifier.notify(app["id"], "uptime", title, text)

    def tick(self, now: float | None = None) -> list[str]:
        """Check every site that has not been looked at for a minute, a few at a time."""
        now = now or time.time()
        due = []
        for app in self.db.list_apps():
            if not app.get("domain") or app.get("mode") == "stack":
                continue
            last = self.db.last_uptime(app["id"])
            if not last or now - datetime.fromisoformat(last).timestamp() >= INTERVAL:
                due.append(app)
        if due:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda a: self._safe(a, now), due))
        if now - self._pruned > 3600:
            self._pruned = now
            self.db.prune_uptime(_iso(now - KEEP_DAYS * 86400))
        return [a["id"] for a in due]

    def _safe(self, app: dict, now: float) -> None:
        try:
            self.check(app, now)
        except Exception:  # noqa: BLE001 - one site must not stop the others
            log.exception("uptime check of %s failed", app["id"])

    def report(self, app_id: str, days: int = 30, now: float | None = None) -> dict:
        now = now or time.time()
        since = _iso(now - days * 86400)
        rows = self.db.uptime_since(app_id, since)
        total = len(rows)
        up = sum(1 for r in rows if r["ok"])
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(r["at"][:10], []).append(r)
        series = []
        for i in range(days):
            day = (datetime.fromtimestamp(now, timezone.utc) - timedelta(days=days - 1 - i)).date().isoformat()
            checks = by_day.get(day, [])
            series.append({"day": day, "checks": len(checks),
                           "uptime": round(100 * sum(1 for c in checks if c["ok"]) / len(checks), 2) if checks else None,
                           "ms": int(sum(c["ms"] for c in checks) / len(checks)) if checks else None})
        hours: dict[str, list] = {}
        for r in rows:
            if r["at"] >= _iso(now - 86400):
                hours.setdefault(r["at"][:13], []).append(r)
        latency = [{"hour": h, "ms": int(sum(c["ms"] for c in cs) / len(cs)), "ok": all(c["ok"] for c in cs)} for h, cs in sorted(hours.items())]
        lat = sorted(r["ms"] for r in rows if r["ok"])
        incidents = self.db.incidents(app_id, since)
        return {"uptime": round(100 * up / total, 3) if total else None, "checks": total,
                "avg_ms": int(sum(lat) / len(lat)) if lat else None, "p95_ms": lat[int(len(lat) * 0.95) - 1] if len(lat) > 20 else None,
                "up_now": bool(rows[-1]["ok"]) if rows else None, "last_check": rows[-1]["at"] if rows else None,
                "days": series, "latency": latency, "incidents": incidents,
                "status_page": self.db.get_setting(f"status_page:{app_id}") == "1"}

    # -- public status page ---------------------------------------------------------------------------

    def status_html(self, app: dict, now: float | None = None) -> str:
        r = self.report(app["id"], 30, now)
        name, domain = html.escape(app["name"]), html.escape(app.get("domain") or "")
        state = r["up_now"]
        head = ("No data yet" if state is None else "All systems operational" if state else "We are having trouble")
        color = "#9ca3af" if state is None else "#10b981" if state else "#ef4444"
        bars = "".join(
            f'<span title="{d["day"]}: {"no data" if d["uptime"] is None else str(d["uptime"]) + " %"}" style="background:'
            f'{"#e5e7eb" if d["uptime"] is None else "#10b981" if d["uptime"] >= 99.9 else "#f59e0b" if d["uptime"] >= 95 else "#ef4444"}"></span>'
            for d in r["days"])
        rows = "".join(
            f'<li><b>{html.escape(i["started"][:16].replace("T", " "))} UTC</b> · '
            f'{"ongoing" if not i["ended"] else "resolved"} · {html.escape(i["reason"] or "")}</li>' for i in r["incidents"][:10]) \
            or "<li>No incident in the last 30 days.</li>"
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} status</title><style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 20px;color:#18181b}}
h1{{font-size:22px;margin:0 0 4px}}.s{{display:flex;align-items:center;gap:10px;font-size:18px;margin:24px 0}}
.d{{width:14px;height:14px;border-radius:50%;background:{color}}}.b{{display:flex;gap:2px;height:36px}}.b span{{flex:1;border-radius:2px}}
small{{color:#71717a}}li{{margin:8px 0;font-size:14px}}ul{{padding-left:18px}}</style></head><body>
<h1>{name}</h1><small>{domain}</small><div class="s"><span class="d"></span>{head}</div>
<p><small>Last 30 days: {"-" if r["uptime"] is None else r["uptime"]} % available</small></p><div class="b">{bars}</div>
<h2 style="font-size:16px;margin-top:32px">Incidents</h2><ul>{rows}</ul><p><small>Updated every minute.</small></p></body></html>"""
