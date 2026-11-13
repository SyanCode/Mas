"""Backups of a CMS site: its content files (from the repository) and what Mas keeps about it (statistics,
cookie proofs, audits, settings). One zip per backup, kept on the server and downloadable.

Not in a backup: notification secrets (sealed with this server's key), visitor hashes (they expire by design)
and the list of CMS commits (their hashes only make sense for the repository as it is now)."""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from mas import __version__
from mas.cms import SCHEMA_FILE, CmsError, CmsManager, safe_repo_path
from mas.db import Database

FORMAT = 1
# table -> has its own autoincrement id (not restored: other sites' rows could collide)
TABLES = {"site_stats": False, "site_goals": False, "consent_stats": False, "consent_log": True,
          "consent_versions": False, "compliance_audits": True, "uptime_checks": False, "uptime_incidents": True,
          "cms_schedule": False}
SKIP_SETTINGS = ("notify", "backup_auto")  # sealed with this server's key: useless (and unsafe) elsewhere
NAME_RE = re.compile(r"^[0-9]{8}-[0-9]{6}(-[a-z0-9]{1,24})?\.zip$")
MAX_TOTAL = 80 * 1024 * 1024
KEEP = 14  # automatic backups kept per site


class BackupError(CmsError):
    pass


def folder(data_dir: str | Path, app_id: str) -> Path:
    return Path(data_dir) / "cms-backups" / app_id


def _data(db: Database, app_id: str) -> dict:
    tables: dict[str, list[dict]] = {}
    with db.tx() as c:
        for name in TABLES:
            tables[name] = [dict(r) for r in c.execute(f"SELECT * FROM {name} WHERE app_id = ?", (app_id,))]
        settings = {r["key"]: r["value"] for r in c.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?", (f"%:{app_id}",))
            if r["key"].split(":", 1)[0] not in SKIP_SETTINGS}
    return {"tables": tables, "settings": settings}


def build(db: Database, manager: CmsManager | None, app_id: str, label: str = "") -> bytes:
    """The zip of one site. The content part is skipped (and said so) when the repository cannot be read."""
    data = _data(db, app_id)
    files: dict[str, bytes] = {}
    content_error = None
    if manager is not None:
        try:
            files = manager.snapshot_files(app_id)
        except CmsError as exc:
            content_error = str(exc)
    manifest = {"format": FORMAT, "app_id": app_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mas_version": __version__, "label": label[:80],
                "rows": {k: len(v) for k, v in data["tables"].items()}, "settings": len(data["settings"]),
                "files": len(files), "content_error": content_error}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("data.json", json.dumps(data))
        for path, blob in files.items():
            z.writestr(f"content/{path}", blob)
    return out.getvalue()


def read(raw: bytes) -> tuple[dict, dict, dict[str, bytes]]:
    """(manifest, data, files) of a backup, refusing anything that is not one of ours."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if "manifest.json" not in names or "data.json" not in names:
                raise BackupError("not a Mas backup")
            if sum(i.file_size for i in z.infolist()) > MAX_TOTAL:
                raise BackupError("backup too large")
            manifest = json.loads(z.read("manifest.json"))
            data = json.loads(z.read("data.json"))
            files = {n[len("content/"):]: z.read(n) for n in names if n.startswith("content/") and not n.endswith("/")}
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
        raise BackupError("not a Mas backup") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(data, dict):
        raise BackupError("this backup comes from a different version of Mas")
    return manifest, data, files


def restore_data(db: Database, app_id: str, data: dict) -> dict[str, int]:
    """Replace what Mas keeps about the site by the backup's. All or nothing."""
    counts: dict[str, int] = {}
    tables = data.get("tables") or {}
    settings = data.get("settings") or {}
    if not isinstance(tables, dict) or not isinstance(settings, dict):
        raise BackupError("damaged backup")
    with db.tx() as c:
        for name, own_id in TABLES.items():
            rows = tables.get(name) or []
            if not isinstance(rows, list):
                raise BackupError("damaged backup")
            known = [r[1] for r in c.execute(f"PRAGMA table_info({name})")]
            c.execute(f"DELETE FROM {name} WHERE app_id = ?", (app_id,))
            n = 0
            for row in rows:
                if not isinstance(row, dict):
                    raise BackupError("damaged backup")
                cols = [k for k in row if k in known and not (own_id and k == "id")]
                values = [row[k] for k in cols]
                if "app_id" in cols:
                    values[cols.index("app_id")] = app_id
                else:
                    cols.append("app_id"); values.append(app_id)
                c.execute(f"INSERT OR REPLACE INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
                n += 1
            counts[name] = n
        for key in [r["key"] for r in c.execute("SELECT key FROM settings WHERE key LIKE ?", (f"%:{app_id}",))
                    if r["key"].split(":", 1)[0] not in SKIP_SETTINGS]:
            c.execute("DELETE FROM settings WHERE key = ?", (key,))
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        for key, value in settings.items():
            if (not isinstance(key, str) or not key.endswith(f":{app_id}") or key.split(":", 1)[0] in SKIP_SETTINGS
                    or not isinstance(value, str)):
                continue
            c.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                      (key, value, now))
    return counts


def checked_files(files: dict[str, bytes]) -> dict[str, bytes]:
    """Only paths the CMS itself could have written."""
    out = {}
    for path, blob in files.items():
        out[path if path == SCHEMA_FILE else safe_repo_path(path, "file")] = blob
    return out


# -- stored backups -----------------------------------------------------------

def save(data_dir: str | Path, app_id: str, raw: bytes, auto: bool = False) -> str:
    d = folder(data_dir, app_id)
    d.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ("-auto" if auto else "") + ".zip"
    n = 1
    while (d / name).exists():
        n += 1
        name = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{'auto' if auto else 'x'}{n}.zip"
    (d / name).write_bytes(raw)
    if auto:
        old = sorted(p for p in d.glob("*-auto*.zip"))[:-KEEP]
        for p in old:
            p.unlink(missing_ok=True)
    return name


def listing(data_dir: str | Path, app_id: str) -> list[dict]:
    d = folder(data_dir, app_id)
    out = []
    for p in sorted(d.glob("*.zip"), reverse=True) if d.is_dir() else []:
        if not NAME_RE.fullmatch(p.name):
            continue
        item = {"name": p.name, "size": p.stat().st_size, "auto": "-auto" in p.name}
        try:
            with zipfile.ZipFile(p) as z:
                m = json.loads(z.read("manifest.json"))
            item.update(created_at=m.get("created_at"), files=m.get("files", 0), rows=sum((m.get("rows") or {}).values()),
                        content_error=m.get("content_error"))
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
            item["broken"] = True
        out.append(item)
    return out


def path_of(data_dir: str | Path, app_id: str, name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise BackupError("unknown backup")
    p = folder(data_dir, app_id) / name
    if not p.is_file():
        raise BackupError("unknown backup")
    return p


def data_dir_of(manager: CmsManager) -> Path:
    return manager.staging.parent


def tick(db: Database, manager: CmsManager) -> list[str]:
    """Once a day, a backup of every site that asked for it (the 14 latest are kept)."""
    done = []
    for a in db.list_apps():
        if db.get_setting(f"backup_auto:{a['id']}") != "1" or not a["repo"] or not a["installation_id"]:
            continue
        last = max((i.get("created_at") or "" for i in listing(data_dir_of(manager), a["id"]) if i["auto"]), default="")
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < 23 * 3600:
            continue
        try:
            save(data_dir_of(manager), a["id"], build(db, manager, a["id"], "automatic"), auto=True)
            done.append(a["id"])
        except Exception:  # a failing site must not stop the others
            continue
    return done
