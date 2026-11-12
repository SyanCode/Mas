"""Notifications: e-mail and webhooks (Slack, Discord, anything that takes JSON) about a site.

Events: ``compliance`` (the audit found new blocking issues or the score fell), ``deploy_failed``,
``scheduled`` (a scheduled entry went live), ``uptime`` (the site went down or came back) and a weekly
``digest`` for the client. The mail server and webhook addresses are kept encrypted; only
administrators set them.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import time
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import urlsplit

from mas.config import Config
from mas.db import Database
from mas.envcrypt import EnvError, load_fernet
from mas.host import Host

log = logging.getLogger(__name__)

EVENTS = ("compliance", "deploy_failed", "scheduled", "uptime", "digest")
SECURITY = ("none", "starttls", "ssl")
DEFAULT_SITE = {"emails": [], "webhook": "", "events": ["compliance", "deploy_failed", "uptime"], "digest": False}
EMAIL_RE = __import__("re").compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")


class NotifyError(Exception):
    pass


class Notifier:
    def __init__(self, db: Database, host: Host, config: Config, stats=None, compliance=None) -> None:
        self.db, self.host, self.config = db, host, config
        self.stats, self.compliance = stats, compliance  # for the weekly digest
        self.opener = urllib.request.urlopen  # replaced in tests
        self.smtp_factory = None  # replaced in tests: (host, port, security) -> connection

    # -- secrets ------------------------------------------------------------------------------------

    def _fernet(self):
        return load_fernet(self.host, self.config.secret_key_path)

    def _seal(self, text: str) -> str:
        return self._fernet().encrypt(text.encode()).decode() if text else ""

    def _open(self, token: str) -> str:
        try:
            return self._fernet().decrypt(token.encode()).decode() if token else ""
        except Exception as exc:  # noqa: BLE001
            raise EnvError("cannot decrypt (secret key changed?)") from exc

    # -- settings -----------------------------------------------------------------------------------

    def smtp(self, reveal: bool = False) -> dict:
        raw = json.loads(self.db.get_setting("smtp") or "{}")
        out = {"host": raw.get("host", ""), "port": raw.get("port", 587), "security": raw.get("security", "starttls"),
               "user": raw.get("user", ""), "from": raw.get("from", ""), "has_password": bool(raw.get("password"))}
        if reveal:
            out["password"] = self._open(raw.get("password", ""))
        return out

    def set_smtp(self, body: dict) -> dict:
        old = json.loads(self.db.get_setting("smtp") or "{}")
        host, port = str(body.get("host", "")).strip(), body.get("port", 587)
        sender, security = str(body.get("from", "")).strip(), body.get("security", "starttls")
        if host and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
            raise NotifyError("port: 1 to 65535")
        if security not in SECURITY:
            raise NotifyError("security: none, starttls or ssl")
        if host and not EMAIL_RE.fullmatch(sender):
            raise NotifyError("sender: an e-mail address")
        password = old.get("password", "")
        if body.get("password"):
            password = self._seal(str(body["password"]))
        if body.get("clear_password"):
            password = ""
        self.db.set_setting("smtp", json.dumps({"host": host, "port": port, "security": security,
                                                "user": str(body.get("user", "")).strip(), "from": sender,
                                                "password": password}))
        return self.smtp()

    def site(self, app_id: str, reveal: bool = False) -> dict:
        raw = json.loads(self.db.get_setting(f"notify:{app_id}") or "null") or {}
        out = {**DEFAULT_SITE, **{k: raw[k] for k in DEFAULT_SITE if k in raw}}
        webhook = self._open(raw.get("webhook_sealed", "")) if raw.get("webhook_sealed") else ""
        out["webhook"] = webhook if reveal else ""
        out["has_webhook"] = bool(webhook)
        return out

    def set_site(self, app_id: str, body: dict) -> dict:
        emails = body.get("emails", [])
        if not isinstance(emails, list) or len(emails) > 10 or any(not isinstance(e, str) or not EMAIL_RE.fullmatch(e.strip()) for e in emails):
            raise NotifyError("emails: up to 10 valid addresses")
        events = body.get("events", [])
        if not isinstance(events, list) or any(e not in EVENTS for e in events):
            raise NotifyError(f"events: {', '.join(EVENTS)}")
        raw = json.loads(self.db.get_setting(f"notify:{app_id}") or "null") or {}
        sealed = raw.get("webhook_sealed", "")
        webhook = str(body.get("webhook", "")).strip()
        if webhook:
            parts = urlsplit(webhook)
            if parts.scheme != "https" or not parts.hostname:
                raise NotifyError("webhook: an https:// address")
            sealed = self._seal(webhook)
        elif body.get("clear_webhook"):
            sealed = ""
        self.db.set_setting(f"notify:{app_id}", json.dumps({
            "emails": [e.strip() for e in emails], "webhook_sealed": sealed, "events": [e for e in EVENTS if e in events],
            "digest": bool(body.get("digest"))}))
        return self.site(app_id)

    # -- sending ------------------------------------------------------------------------------------

    def send_email(self, to: list[str], subject: str, text: str) -> None:
        cfg = self.smtp(reveal=True)
        if not cfg["host"] or not to:
            raise NotifyError("the mail server is not set up")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = cfg["from"], ", ".join(to), subject
        msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
        msg.set_content(text)
        if self.smtp_factory:
            conn = self.smtp_factory(cfg["host"], cfg["port"], cfg["security"])
        elif cfg["security"] == "ssl":
            conn = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20, context=ssl.create_default_context())
        else:
            conn = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
        try:
            if cfg["security"] == "starttls" and not self.smtp_factory:
                conn.starttls(context=ssl.create_default_context())
            if cfg["user"]:
                conn.login(cfg["user"], cfg["password"])
            conn.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifyError(f"e-mail: {exc}") from None
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001
                pass

    def send_webhook(self, url: str, title: str, text: str, event: str, site: str) -> None:
        body = json.dumps({"text": f"*{title}*\n{text}", "content": f"**{title}**\n{text}", "title": title,
                           "message": text, "event": event, "site": site}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "mas"})
        try:
            with self.opener(req, timeout=15) as resp:  # noqa: S310 - https only, set by an administrator
                if resp.status >= 400:
                    raise NotifyError(f"webhook answered {resp.status}")
        except OSError as exc:
            raise NotifyError(f"webhook: {exc}") from None

    def notify(self, app_id: str, event: str, title: str, text: str) -> list[str]:
        """Tell the people and tools set for this site; returns what was reached. Never raises."""
        cfg = self.site(app_id, reveal=True)
        if event not in cfg["events"] and not (event == "digest" and cfg["digest"]):
            return []
        app = self.db.get_app(app_id) or {"name": app_id}
        reached = []
        if cfg["webhook"]:
            try:
                self.send_webhook(cfg["webhook"], f"{app['name']}: {title}", text, event, app_id)
                reached.append("webhook")
            except (NotifyError, EnvError) as exc:
                log.warning("webhook for %s failed: %s", app_id, exc)
        if cfg["emails"]:
            try:
                self.send_email(cfg["emails"], f"[{app['name']}] {title}", text)
                reached.append("email")
            except (NotifyError, EnvError) as exc:
                log.warning("e-mail for %s failed: %s", app_id, exc)
        self.db.audit("notifier", "cms-notify", bool(reached) or not (cfg["webhook"] or cfg["emails"]), service_id=app_id,
                      detail=f"{event}: {', '.join(reached) or 'nobody reached'}")
        return reached

    # -- what the background loop checks ---------------------------------------------------------------

    def digest_text(self, app: dict, today: date | None = None) -> str:
        today = today or date.today()
        lines = [f"Weekly report for {app['name']} ({app.get('domain') or 'no domain'})", ""]
        report = self.stats.report(app, 7, today) if self.stats and app.get("domain") else None
        if report and report["totals"]:
            t = report["totals"]
            lines += [f"Visits (7 days): {t['pageviews']} page views, {t['visitors']} visitors",
                      f"Conversion rate: {report['conversion']['rate']}%"]
            if report["top_pages"]:
                lines.append("Top pages: " + ", ".join(f"{p['path']} ({p['views']})" for p in report["top_pages"][:3]))
            if report.get("sources"):
                lines.append("Main sources: " + ", ".join(f"{s['name'] or 'direct'} ({s['visitors']})" for s in report["sources"][:3]))
        audit = self.compliance.latest(app["id"]) if self.compliance else None
        if audit:
            lines.append(f"Compliance: {audit['score']}/100 ({audit['counts']['critical']} blocking, {audit['counts']['warning']} to improve)")
        since = (datetime.now() - timedelta(days=7)).isoformat()
        changes = [e for e in self.db.cms_activity([app["id"]], 200) if e["at"] >= since and e["ok"]]
        lines.append(f"Changes this week: {len(changes)}")
        return "\n".join(lines) + "\n"

    def tick(self, now: float | None = None) -> list[str]:
        """Failed deployments, and the weekly digest on Monday morning."""
        now = now or time.time()
        done = []
        stamp = datetime.fromtimestamp(now)
        for app in self.db.list_apps():
            raw = json.loads(self.db.get_setting(f"notify:{app['id']}") or "null")
            if not raw:
                continue
            cfg = self.site(app["id"])
            if "deploy_failed" in cfg["events"]:
                mark = self.db.get_setting(f"notify:last_failed:{app['id']}")
                recent = self.db.list_releases(app["id"], 5)
                if mark is None:  # the first look only sets the mark: old failures are not news
                    self.db.set_setting(f"notify:last_failed:{app['id']}", str(max((r["id"] for r in recent), default=0)))
                else:
                    failed = [r for r in recent if r["status"] == "failed" and r["id"] > int(mark)]
                    if failed:
                        self.db.set_setting(f"notify:last_failed:{app['id']}", str(max(r["id"] for r in failed)))
                        r = failed[0]
                        self.notify(app["id"], "deploy_failed", "Deployment failed",
                                    f"The release #{r['id']} ({r.get('message') or r['trigger']}) failed: {r.get('error') or 'see the build log'}.")
                        done.append(f"failed:{app['id']}")
            if cfg["digest"] and stamp.weekday() == 0 and stamp.hour >= 8:
                week = stamp.strftime("%G-%V")
                if self.db.get_setting(f"notify:digest:{app['id']}") != week:
                    self.db.set_setting(f"notify:digest:{app['id']}", week)
                    self.notify(app["id"], "digest", "Weekly report", self.digest_text(app, stamp.date()))
                    done.append(f"digest:{app['id']}")
        return done
