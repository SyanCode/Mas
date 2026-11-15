"""File sharing: host a big file on the server and give anyone a link to download it, optionally behind a password.

- An administrator creates a share (name, size, optional password, expiry, download limit), then sends the
  file as the raw body of one request (streamed to disk, never held in memory). Files live in
  ``<data dir>/shares/<id>`` (mode 0600), outside every web root: only the routes below can read them.
- The link is ``/d/<id>``, ``id`` being 128 random bits. With a password the page asks for it; the right
  password gives a signed link (HMAC, one share, one hour) to the file, so that a download can be paused and
  resumed (HTTP ranges). ``curl -u :PASSWORD`` works too. Passwords are stored as argon2 hashes.
- Wrong passwords are rate limited per address and share. A share ends by itself at its expiry date or after
  its download limit; the file is then deleted.
- Files are always served as attachments (``application/octet-stream``, ``nosniff``, sandboxed CSP): an
  uploaded HTML page can never run on the dashboard's origin.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import shutil
import time
from html import escape
from pathlib import Path

from mas import auth
from mas.db import Database

SECRET_KEY = "share_secret"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
MAX_NAME = 200
MAX_EXPIRES_HOURS = 24 * 365
MAX_DOWNLOADS = 1_000_000
MIN_PASSWORD = 6
LINK_TTL = 3600           # seconds a signed link (after the right password) stays valid
RESERVE = 1024 ** 3       # free disk space always left untouched by uploads
CHUNK = 1024 * 1024


class ShareError(Exception):
    pass


def folder(db: Database) -> Path:
    return Path(db.path).parent / "shares"


def file_path(db: Database, share_id: str) -> Path:
    if not ID_RE.match(share_id):
        raise ShareError("invalid id")
    return folder(db) / share_id


def clean_name(name: str) -> str:
    """A name safe for a header and a file system: no path, no control characters."""
    name = re.sub(r"[\x00-\x1f\x7f\\/\"]", "", (name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip().lstrip(".")
    return name[:MAX_NAME] or "file"


def _now() -> float:
    return time.time()


# -- records ---------------------------------------------------------------------------------------

def create(db: Database, actor: str, name: str, size: int, password: str | None, expires_hours: int | None,
           max_downloads: int | None) -> dict:
    if size <= 0:
        raise ShareError("the file is empty")
    if expires_hours is not None and not 1 <= expires_hours <= MAX_EXPIRES_HOURS:
        raise ShareError("invalid expiry")
    if max_downloads is not None and not 1 <= max_downloads <= MAX_DOWNLOADS:
        raise ShareError("invalid download limit")
    if password and len(password) < MIN_PASSWORD:
        raise ShareError(f"the password must have at least {MIN_PASSWORD} characters")
    free = shutil.disk_usage(_existing(folder(db))).free
    if size > free - RESERVE:
        raise ShareError("not enough free disk space")
    share_id = secrets.token_urlsafe(16)
    with db.tx() as c:
        c.execute(
            "INSERT INTO shares (id, name, size, password_hash, expires_at, max_downloads, downloads, ready, "
            "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
            (share_id, clean_name(name), size, auth.hash_password(password) if password else None,
             _now() + expires_hours * 3600 if expires_hours else None, max_downloads, _now(), actor))
    return get(db, share_id)


def _existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _row(row) -> dict:
    return {"id": row["id"], "name": row["name"], "size": row["size"], "has_password": bool(row["password_hash"]),
            "expires_at": row["expires_at"], "max_downloads": row["max_downloads"], "downloads": row["downloads"],
            "ready": bool(row["ready"]), "created_at": row["created_at"], "created_by": row["created_by"],
            "last_download": row["last_download"]}


def get(db: Database, share_id: str) -> dict | None:
    row = _raw(db, share_id)
    return _row(row) if row else None


def _raw(db: Database, share_id: str):
    if not ID_RE.match(share_id or ""):
        return None
    with db.tx() as c:
        return c.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()


def listing(db: Database) -> list[dict]:
    with db.tx() as c:
        rows = c.execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def alive(row, now: float | None = None) -> bool:
    """Ready, not expired, download limit not reached."""
    now = _now() if now is None else now
    if row is None or not row["ready"]:
        return False
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return False
    return row["max_downloads"] is None or row["downloads"] < row["max_downloads"]


def update(db: Database, share_id: str, *, password: str | None = None, remove_password: bool = False,
           expires_hours: int | None = None, no_expiry: bool = False, max_downloads: int | None = None,
           no_limit: bool = False) -> dict:
    if _raw(db, share_id) is None:
        raise ShareError("unknown share")
    sets, args = [], []
    if remove_password:
        sets.append("password_hash = NULL")
    elif password:
        if len(password) < MIN_PASSWORD:
            raise ShareError(f"the password must have at least {MIN_PASSWORD} characters")
        sets.append("password_hash = ?")
        args.append(auth.hash_password(password))
    if no_expiry:
        sets.append("expires_at = NULL")
    elif expires_hours is not None:
        if not 1 <= expires_hours <= MAX_EXPIRES_HOURS:
            raise ShareError("invalid expiry")
        sets.append("expires_at = ?")
        args.append(_now() + expires_hours * 3600)
    if no_limit:
        sets.append("max_downloads = NULL")
    elif max_downloads is not None:
        if not 1 <= max_downloads <= MAX_DOWNLOADS:
            raise ShareError("invalid download limit")
        sets.append("max_downloads = ?")
        args.append(max_downloads)
    if sets:
        with db.tx() as c:
            c.execute(f"UPDATE shares SET {', '.join(sets)} WHERE id = ?", (*args, share_id))  # noqa: S608 (fixed columns)
    return get(db, share_id)


def delete(db: Database, share_id: str) -> bool:
    if _raw(db, share_id) is None:
        return False
    with db.tx() as c:
        c.execute("DELETE FROM shares WHERE id = ?", (share_id,))
    for path in (file_path(db, share_id), file_path(db, share_id).with_suffix(".part")):
        path.unlink(missing_ok=True)
    return True


def count_download(db: Database, share_id: str) -> bool:
    """Take one download if the limit allows it (atomic), so two parallel requests cannot both pass the last one."""
    with db.tx() as c:
        cur = c.execute(
            "UPDATE shares SET downloads = downloads + 1, last_download = ? WHERE id = ? AND ready = 1 "
            "AND (max_downloads IS NULL OR downloads < max_downloads) AND (expires_at IS NULL OR expires_at > ?)",
            (_now(), share_id, _now()))
        return cur.rowcount == 1


def finish_upload(db: Database, share_id: str, part: Path, received: int) -> dict:
    row = _raw(db, share_id)
    if row is None:
        raise ShareError("unknown share")
    if received != row["size"]:
        part.unlink(missing_ok=True)
        raise ShareError(f"received {received} bytes, announced {row['size']}")
    os.replace(part, file_path(db, share_id))
    with db.tx() as c:
        c.execute("UPDATE shares SET ready = 1 WHERE id = ?", (share_id,))
    return get(db, share_id)


def sweep(db: Database) -> int:
    """Delete what ended (expired, limit reached) and uploads abandoned for a day; returns how many."""
    now = _now()
    gone = 0
    with db.tx() as c:
        rows = c.execute("SELECT * FROM shares").fetchall()
    for row in rows:
        ended = (row["ready"] and not alive(row, now)) or (not row["ready"] and row["created_at"] < now - 86400)
        if ended:
            gone += delete(db, row["id"])
    root = folder(db)
    if root.is_dir():  # files nobody owns (a crash between the file and its record)
        known = {r["id"] for r in rows}
        for path in root.iterdir():
            if path.stem not in known and now - path.stat().st_mtime > 86400:
                path.unlink(missing_ok=True)
    return gone


# -- passwords and signed links ----------------------------------------------------------------------

def check_password(row, password: str) -> bool:
    return bool(row["password_hash"]) and auth.verify_password(row["password_hash"], password or "")


def _secret(db: Database) -> bytes:
    value = db.get_setting(SECRET_KEY)
    if not value:
        value = secrets.token_hex(32)
        db.set_setting(SECRET_KEY, value)
    return value.encode()


def _sign(db: Database, share_id: str, expires: int, row) -> str:
    # bound to the current password: changing it revokes every link already given
    bind = hashlib.sha256((row["password_hash"] or "").encode()).hexdigest()[:16]
    mac = hmac.new(_secret(db), f"{share_id}.{expires}.{bind}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def make_link_token(db: Database, row, now: float | None = None) -> str:
    expires = int((_now() if now is None else now) + LINK_TTL)
    return f"{expires}.{_sign(db, row['id'], expires, row)}"


def check_link_token(db: Database, row, token: str, now: float | None = None) -> bool:
    try:
        expires_text, sig = (token or "").split(".", 1)
        expires = int(expires_text)
    except ValueError:
        return False
    if expires < (_now() if now is None else now):
        return False
    return hmac.compare_digest(sig, _sign(db, row["id"], expires, row))


def basic_password(header: str) -> str | None:
    """The password of an ``Authorization: Basic`` header (any user name), for curl and wget."""
    if header[:6].lower() != "basic ":
        return None
    try:
        return base64.b64decode(header[6:].strip()).decode().partition(":")[2]
    except (ValueError, UnicodeDecodeError):
        return None


# -- the public pages ----------------------------------------------------------------------------------

def human_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def page(row, *, error: str = "", token: str = "") -> str:
    """The download page: name, size, and a password field when one is needed. No script, no external resource."""
    name, size = escape(row["name"]), human_size(row["size"])
    if row["password_hash"] and not token:
        form = (f'<form method="post" autocomplete="off"><label for="p">Password</label>'
                f'<input id="p" name="password" type="password" required autofocus>'
                f'<button type="submit">Download</button></form>')
    else:
        href = f"/d/{row['id']}/file" + (f"?t={escape(token)}" if token else "")
        form = f'<a class="btn" href="{href}">Download</a>'
    note = f'<p class="err" role="alert">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{name}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f5f1;color:#1c1b19;font:16px/1.5 system-ui,sans-serif}}
main{{background:#fff;border:1px solid #e4e1d8;border-radius:14px;padding:32px;width:min(92vw,420px);box-shadow:0 1px 3px rgba(0,0,0,.05)}}
h1{{font:600 20px/1.3 Georgia,serif;margin:0 0 4px;word-break:break-word}}p{{margin:0 0 20px;color:#6b675e}}
label{{display:block;font-size:13px;color:#6b675e;margin-bottom:4px}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cfcbbf;border-radius:8px;font:inherit;margin-bottom:12px}}
button,.btn{{display:block;width:100%;box-sizing:border-box;text-align:center;padding:10px 12px;border:0;border-radius:8px;background:#4f46e5;color:#fff;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}}
.err{{color:#b3261e;margin:0 0 12px}}
</style></head><body><main><h1>{name}</h1><p>{size}</p>{note}{form}</main></body></html>"""


GONE_PAGE = page({"name": "Link not available", "size": 0, "password_hash": None, "id": "x"}).replace(
    '<p>0 B</p>', "<p>This link has expired or does not exist.</p>").replace(
    '<a class="btn" href="/d/x/file">Download</a>', "")
