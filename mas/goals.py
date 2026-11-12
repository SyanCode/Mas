"""Conversions: what visitors click, counted without cookies.

A site adds one line (``<script defer src="https://<mas>/t.js"></script>``). The script sends a
named goal when a visitor clicks something marked ``data-mas-goal="book"`` (or a ``tel:`` /
``mailto:`` link), or submits a form marked the same way. The visitor is the same anonymous
hash the access logs use (day + address + browser, never stored), so a goal joins the day's
visitors: conversion rate = visitors who reached a goal / visitors.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urlsplit

from mas.db import Database
from mas.sitestats import BOT_RE

GOAL_RE = re.compile(r"^[a-z0-9_.:-]{1,40}$")
ANY = "_any"  # every goal together: one visitor counts once, whatever they clicked
MAX_GOALS_PER_DAY = 30  # a site cannot fill the database with invented names
MAX_BODY = 512

SCRIPT = r"""(function () {
  var s = document.currentScript;
  if (!s || navigator.doNotTrack === "1") return;
  var endpoint = new URL(s.src).origin + "/t/e";
  function send(goal) {
    if (!/^[a-z0-9_.:-]{1,40}$/.test(goal)) return;
    try {
      fetch(endpoint, { method: "POST", body: JSON.stringify({ g: goal }), keepalive: true, mode: "no-cors",
        credentials: "omit", headers: { "Content-Type": "text/plain" } });
    } catch (e) {}
  }
  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest && e.target.closest("[data-mas-goal],a[href^='tel:'],a[href^='mailto:']");
    if (!el || el.tagName === "FORM") return;
    var goal = el.getAttribute("data-mas-goal");
    if (!goal) goal = /^tel:/i.test(el.getAttribute("href")) ? "tel" : "mailto";
    send(goal);
  }, true);
  document.addEventListener("submit", function (e) {
    var f = e.target && e.target.closest && e.target.closest("[data-mas-goal]");
    if (f) send(f.getAttribute("data-mas-goal"));
  }, true);
  window.mas = send;
})();
"""


def app_for_origin(db: Database, origin: str) -> dict | None:
    """The app whose domain (or alias) is the origin of the page that sent the goal."""
    host = (urlsplit(origin).hostname or "").lower()
    if not host:
        return None
    for app in db.list_apps():
        names = [app.get("domain")] + (list(_aliases(app)))
        if host in {n.lower() for n in names if n}:
            return app
    return None


def _aliases(app: dict) -> list[str]:
    import json
    try:
        value = app.get("aliases")
        found = json.loads(value) if isinstance(value, str) else value
        return [a for a in (found or []) if isinstance(a, str)]
    except ValueError:
        return []


def record(db: Database, origin: str, ip: str, agent: str, goal: str, today: date | None = None) -> bool:
    """Count one goal; False when it is ignored (unknown site, bot, bad name, too many names)."""
    if not GOAL_RE.fullmatch(goal) or goal == ANY or BOT_RE.search(agent or ""):
        return False
    app = app_for_origin(db, origin)
    if app is None:
        return False
    day = (today or date.today()).isoformat()
    who = hashlib.blake2b(f"{day}|{ip}|{agent}".encode(), digest_size=8).digest()
    return db.add_goal(app["id"], day, goal, who, MAX_GOALS_PER_DAY)
