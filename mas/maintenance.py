"""Maintenance mode of the sites: nginx answers a page of your choice (503 + Retry-After) to everyone
but the addresses you allow, while the app keeps running behind.

- Pages live in ``<pages_dir>/maintenance/<page>/`` (index.html and its files), served by nginx under
  ``/__mas_maintenance/``. A page is either written by Mas from a title and a message, or imported:
  one HTML file or a ZIP of a folder with an index.html (a ``<base>`` is added so that its relative
  links work from any URL of the site).
- Turning it on writes two small files and reloads nginx (after ``nginx -t``); the vhost of the app is
  not rewritten. ``/etc/nginx/conf.d/mas-maintenance.conf`` holds, for every site in maintenance, a
  ``geo`` of the allowed addresses and a ``map`` that says whether a request gets the page;
  ``/etc/nginx/mas-maintenance/<app>.conf`` is included by the vhost (``include …[.]conf``: no file,
  no maintenance) and returns 503 with the page. The page itself and ACME challenges always pass.
- ``until``: the maintenance ends by itself at that time (checked every minute).
"""

from __future__ import annotations

import html
import io
import ipaddress
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath

from mas.config import Config
from mas.db import Database
from mas.deploy import DeployError, Deployer, vhost_paths
from mas.host import Host

MAPS_PATH = "/etc/nginx/conf.d/mas-maintenance.conf"
SNIPPETS_DIR = "/etc/nginx/mas-maintenance"
PAGES_SETTING = "maintenance-pages"
STATE = "maintenance:"           # setting maintenance:<app id> = JSON {page, allow, since, until, by}
URL_PREFIX = "/__mas_maintenance/"
PAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_FILES = 300
MAX_BYTES = 20 * 1024 * 1024     # uncompressed, for an imported ZIP
MAX_ALLOW = 50
RETRY_AFTER = 600


class MaintenanceError(Exception):
    pass


def _var(app_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", app_id.lower())


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "page"


def check_allow(entries: list[str]) -> list[str]:
    out = []
    for raw in entries:
        entry = raw.strip()
        if not entry:
            continue
        try:
            out.append(str(ipaddress.ip_network(entry, strict=False)))
        except ValueError:
            raise MaintenanceError(f"not an IP address or a network: {entry!r}") from None
    if len(out) > MAX_ALLOW:
        raise MaintenanceError(f"{MAX_ALLOW} addresses at most")
    return out


def with_base(text: str) -> str:
    """Relative links of the page resolve under /__mas_maintenance/<page>/ whatever the URL asked."""
    if re.search(r"<base\s", text, re.I):
        return text
    tag = f'<base href="{URL_PREFIX}">'
    if re.search(r"<head[^>]*>", text, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + tag, text, count=1, flags=re.I)
    return tag + text


SIMPLE_PAGE = """<!doctype html>
<html lang="@@LANG@@">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>@@TITLE@@</title>
<style>
  :root { color-scheme: light dark; --bg: #f6f7f9; --fg: #18181b; --muted: #52525b; --card: #ffffffcc; }
  @media (prefers-color-scheme: dark) { :root { --bg: #050608; --fg: #f4f4f5; --muted: #a1a1aa; --card: #ffffff0a; } }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: var(--bg);
         color: var(--fg); font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 34rem; text-align: center; padding: 40px 32px; border-radius: 24px; background: var(--card);
         border: 1px solid #8881; box-shadow: 0 30px 80px -40px #0008; }
  .dot { width: 12px; height: 12px; border-radius: 50%; margin: 0 auto 24px; background: @@ACCENT@@;
         box-shadow: 0 0 0 6px color-mix(in srgb, @@ACCENT@@ 25%, transparent); animation: pulse 2s infinite; }
  @keyframes pulse { 50% { box-shadow: 0 0 0 12px transparent; } }
  h1 { margin: 0 0 12px; font-size: 1.7rem; letter-spacing: -0.02em; }
  p { margin: 0; color: var(--muted); white-space: pre-line; }
  .back { margin-top: 20px; font-size: .9rem; }
</style>
</head>
<body>
<main>
  <div class="dot"></div>
  <h1>@@TITLE@@</h1>
  <p>@@MESSAGE@@</p>
  @@BACK@@
</main>
</body>
</html>
"""


def simple_page(title: str, message: str, back: str = "", accent: str = "#6366f1", lang: str = "fr") -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent or ""):
        accent = "#6366f1"
    out = SIMPLE_PAGE
    for key, value in {"LANG": html.escape(lang[:5] or "fr"), "TITLE": html.escape(title), "ACCENT": accent,
                       "MESSAGE": html.escape(message),
                       "BACK": f'<p class="back">{html.escape(back)}</p>' if back else ""}.items():
        out = out.replace(f"@@{key}@@", value)
    return out


class Maintenance:
    def __init__(self, host: Host, config: Config, db: Database, deployer: Deployer) -> None:
        self.host, self.config, self.db, self.deployer = host, config, db, deployer
        self.root = f"{config.pages_dir.rstrip('/')}/maintenance"

    # -- pages ------------------------------------------------------------------------------

    def pages(self) -> dict[str, dict]:
        try:
            value = json.loads(self.db.get_setting(PAGES_SETTING) or "{}")
        except ValueError:
            value = {}
        if "default" not in value:
            value["default"] = {"name": "Maintenance", "kind": "simple", "title": "Site en maintenance",
                                "message": "Nous faisons quelques améliorations. Revenez dans un instant.",
                                "back": "", "accent": "#6366f1"}
        return value

    def _save_pages(self, pages: dict) -> None:
        self.db.set_setting(PAGES_SETTING, json.dumps(pages))

    def _page_dir(self, page_id: str) -> str:
        if not PAGE_ID_RE.fullmatch(page_id):
            raise MaintenanceError("invalid page name")
        return f"{self.root}/{page_id}"

    def _write_simple(self, page_id: str, meta: dict) -> None:
        folder = self._page_dir(page_id)
        self.deployer._mkdir_public(self.config.pages_dir)
        self.deployer._mkdir_public(self.root)
        self.deployer._mkdir_public(folder)
        content = simple_page(meta["title"], meta["message"], meta.get("back", ""), meta.get("accent", "#6366f1"))
        self.host.write_text(f"{folder}/index.html", with_base(content), mode=0o644)

    def ensure_page(self, page_id: str) -> None:
        """The files of the page are there (the default one is written the first time it is used)."""
        pages = self.pages()
        if page_id not in pages:
            raise MaintenanceError("unknown page")
        if not self.host.exists(f"{self._page_dir(page_id)}/index.html"):
            if pages[page_id]["kind"] != "simple":
                raise MaintenanceError("the files of this page are missing: import it again")
            self._write_simple(page_id, pages[page_id])

    def save_simple(self, page_id: str | None, name: str, title: str, message: str, back: str = "",
                    accent: str = "#6366f1") -> str:
        pages = self.pages()
        page_id = page_id or self._free_id(slug(name), pages)
        if page_id in pages and pages[page_id]["kind"] != "simple":
            raise MaintenanceError("an imported page is replaced by importing it again")
        if not title.strip():
            raise MaintenanceError("a title is needed")
        meta = {"name": name.strip()[:60] or title[:60], "kind": "simple", "title": title.strip()[:120],
                "message": message.strip()[:2000], "back": back.strip()[:200], "accent": accent}
        self._write_simple(page_id, meta)
        pages[page_id] = meta
        self._save_pages(pages)
        self._refresh_users(page_id)
        return page_id

    def _free_id(self, base: str, pages: dict) -> str:
        page_id, n = base, 2
        while page_id in pages:
            page_id, n = f"{base[:36]}-{n}", n + 1
        return page_id

    def import_page(self, name: str, data: bytes, page_id: str | None = None) -> str:
        """One HTML file, or a ZIP with an index.html (at its root or in a single folder)."""
        files: dict[str, bytes] = {}
        if data[:4] == b"PK\x03\x04":
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise MaintenanceError("not a valid ZIP file") from None
            entries = [i for i in archive.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")]
            if len(entries) > MAX_FILES:
                raise MaintenanceError(f"{MAX_FILES} files at most")
            if sum(i.file_size for i in entries) > MAX_BYTES:
                raise MaintenanceError("the page is too big once unzipped (20 MB at most)")
            names = [PurePosixPath(i.filename) for i in entries]
            tops = {n.parts[0] for n in names if len(n.parts) > 1}
            strip = len(tops) == 1 and not any(len(n.parts) == 1 for n in names)  # everything in one folder
            for info, path in zip(entries, names):
                parts = path.parts[1:] if strip else path.parts
                if not parts or any(p in ("..", "") or p.startswith(".") for p in parts) or path.is_absolute():
                    continue  # no way out of the page folder, no hidden files
                files["/".join(parts)] = archive.read(info)
        else:
            files["index.html"] = data
        index = files.get("index.html")
        if index is None:
            raise MaintenanceError("the ZIP has no index.html")
        try:
            text = index.decode("utf-8")
        except UnicodeDecodeError:
            text = index.decode("latin-1")
        if "<" not in text:
            raise MaintenanceError("this does not look like an HTML page")
        files["index.html"] = with_base(text).encode("utf-8")
        pages = self.pages()
        page_id = page_id or self._free_id(slug(name), pages)
        folder = self._page_dir(page_id)
        staging = f"{folder}.mas-new"
        self.host.remove(staging)
        self.deployer._mkdir_public(self.config.pages_dir)
        self.deployer._mkdir_public(self.root)
        for rel, content in files.items():
            target = f"{staging}/{rel}"
            parent = target.rsplit("/", 1)[0]
            self.deployer._mkdir_public(parent)
            self.host.write_bytes(target, content, mode=0o644)
        self.host.remove(folder)
        self.host.rename(staging, folder)
        self.host.run(["chmod", "-R", "a+rX", self.deployer._r(folder)])
        pages[page_id] = {"name": name.strip()[:60] or page_id, "kind": "imported", "files": len(files)}
        self._save_pages(pages)
        return page_id

    def delete_page(self, page_id: str) -> None:
        if page_id == "default":
            raise MaintenanceError("the default page stays (you can change its text)")
        users = [a["id"] for a, s in self._states() if s["page"] == page_id]
        if users:
            raise MaintenanceError(f"in use by {', '.join(users)}")
        pages = self.pages()
        pages.pop(page_id, None)
        self._save_pages(pages)
        self.host.remove(self._page_dir(page_id))

    def page_file(self, page_id: str, rel: str) -> bytes | None:
        folder = self._page_dir(page_id)
        parts = PurePosixPath(rel or "index.html").parts
        if any(p in ("..", "") or p.startswith(".") for p in parts):
            return None
        return self.host.read_bytes(f"{folder}/{'/'.join(parts)}")

    # -- sites ------------------------------------------------------------------------------

    def state(self, app_id: str) -> dict | None:
        try:
            return json.loads(self.db.get_setting(STATE + app_id) or "null")
        except ValueError:
            return None

    def _states(self) -> list[tuple[dict, dict]]:
        out = []
        for app in self.db.list_apps():
            st = self.state(app["id"])
            if st:
                out.append((app, st))
        return out

    def supported(self, app: dict) -> bool:
        """Mas writes the nginx site of this app (deployed apps and services of the catalog)."""
        return bool(app.get("domain")) and app.get("mode") in ("managed", "stack") \
            and self.host.exists(vhost_paths(app["id"])[0])

    def enable(self, app_id: str, page_id: str, allow: list[str], until: str | None, actor: str) -> dict:
        app = self.db.get_app(app_id)
        if app is None:
            raise MaintenanceError("unknown app")
        if not self.supported(app):
            raise MaintenanceError("maintenance applies to the sites Mas serves (an app or a service with a domain)")
        allow = check_allow(allow)
        if until:
            try:
                end = datetime.fromisoformat(until.replace("Z", "+00:00"))
            except ValueError:
                raise MaintenanceError("invalid end date") from None
            if end.tzinfo is None:
                end = end.astimezone()
            if end.timestamp() <= time.time():
                raise MaintenanceError("the end date is in the past")
            until = end.astimezone(timezone.utc).isoformat(timespec="minutes")
        self.ensure_page(page_id)
        previous = self.state(app_id)
        state = {"page": page_id, "allow": allow, "until": until, "by": actor,
                 "since": (previous or {}).get("since") or datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.db.set_setting(STATE + app_id, json.dumps(state))
        try:
            self.apply()
        except DeployError:
            self.db.set_setting(STATE + app_id, json.dumps(previous) if previous else None)
            raise
        return state

    def disable(self, app_id: str) -> None:
        if self.state(app_id) is None:
            return
        previous = self.db.get_setting(STATE + app_id)
        self.db.set_setting(STATE + app_id, None)
        try:
            self.apply()
        except DeployError:
            self.db.set_setting(STATE + app_id, previous)
            raise

    def _refresh_users(self, page_id: str) -> None:
        if any(s["page"] == page_id for _, s in self._states()):
            self.apply()

    def tick(self, now: float | None = None) -> list[str]:
        """End the maintenances whose time is over."""
        now = now or time.time()
        ended = []
        for app, st in self._states():
            until = st.get("until")
            if until and datetime.fromisoformat(until).timestamp() <= now:
                self.db.set_setting(STATE + app["id"], None)
                ended.append(app["id"])
        if ended:
            self.apply()
            for app_id in ended:
                self.db.audit("mas", "maintenance-end", True, service_id=app_id, detail="end time reached")
        return ended

    # -- nginx ------------------------------------------------------------------------------

    def _maps(self, states: list[tuple[dict, dict]]) -> str:
        out = ["# Generated by Mas: sites in maintenance (who gets the maintenance page). Changes are overwritten.\n"]
        for app, st in states:
            v = _var(app["id"])
            allowed = "".join(f"    {net} 0;\n" for net in st["allow"])
            out.append(f"geo $mas_maint_ip_{v} {{\n    default 1;\n{allowed}}}\n")
            out.append(f'map "$mas_maint_ip_{v}:$uri" $mas_maint_{v} {{\n    volatile;\n    default 0;\n'
                       f'    "~^1:/(__mas_maintenance/|\\.well-known/acme-challenge/)" 0;\n    "~^1:" 1;\n}}\n')
        return "\n".join(out)

    def _snippet(self, app: dict, st: dict) -> str:
        v = _var(app["id"])
        folder = self._page_dir(st["page"])
        return (f"# Generated by Mas: {app['id']} is in maintenance (page {st['page']}).\n"
                f"if ($mas_maint_{v}) {{\n    return 503;\n}}\n"
                f"error_page 503 {URL_PREFIX}index.html;\n"
                f"location ^~ {URL_PREFIX} {{\n    alias {folder}/;\n"
                f'    add_header Cache-Control "no-store" always;\n'
                f'    add_header Retry-After "{RETRY_AFTER}" always;\n}}\n')

    def _include_vhost(self, app_id: str) -> str | None:
        """The vhost of the app includes its maintenance file (added once to the vhosts written before)."""
        path = vhost_paths(app_id)[0]
        text = self.host.read_text(path) or ""
        line = include_line(app_id)
        if line.strip() in text or not text.startswith("# Generated by Mas"):
            return None
        marker = f"    error_log /var/log/nginx/mas-app-{app_id}.error.log;\n"
        if marker not in text:
            return None
        return text.replace(marker, marker + line)

    def apply(self) -> None:
        """Write the maps and the snippets of every site in maintenance; test, reload; restore if refused."""
        states = self._states()
        wanted = {f"{SNIPPETS_DIR}/{app['id']}.conf": self._snippet(app, st) for app, st in states}
        changes: dict[str, str | None] = {MAPS_PATH: self._maps(states) if states else None}
        for path in self.host.glob(f"{SNIPPETS_DIR}/*.conf"):
            if path not in wanted:
                changes[path] = None
        changes.update(wanted)
        for app, _ in states:
            patched = self._include_vhost(app["id"])
            if patched is not None:
                changes[vhost_paths(app["id"])[0]] = patched
        before = {path: self.host.read_text(path) for path in changes}
        if all(before[p] == c for p, c in changes.items()):
            return
        self.host.makedirs(SNIPPETS_DIR)
        for path, content in changes.items():
            if content is None:
                self.host.remove(path)
            else:
                self.host.write_text(path, content, mode=0o644)
        test = self.host.run(["nginx", "-t"], timeout=60)
        if test is None or not test.ok:
            for path, content in before.items():
                if content is None:
                    self.host.remove(path)
                else:
                    self.host.write_text(path, content, mode=0o644)
            detail = (test.stderr if test else "nginx not found").strip()
            raise DeployError(f"nginx -t failed, previous configuration restored:\n{detail}")
        self.host.run(["systemctl", "reload", "nginx"], timeout=60)

    def overview(self) -> dict:
        apps = []
        for app in self.db.list_apps():
            if not app.get("domain") or app.get("replica_of"):
                continue
            apps.append({"id": app["id"], "name": app["name"], "domain": app["domain"], "mode": app["mode"],
                         "supported": self.supported(app), "maintenance": self.state(app["id"])})
        pages = [{"id": k, **v} for k, v in self.pages().items()]
        return {"pages": sorted(pages, key=lambda p: (p["id"] != "default", p["name"].lower())), "apps": apps}


def include_line(app_id: str) -> str:
    # "[.]conf" is a pattern: no file (no maintenance) is not an error for nginx
    return f"    include {SNIPPETS_DIR}/{app_id}[.]conf;\n"
