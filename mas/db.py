"""SQLite storage: admin account, sessions, service metadata and the audit log."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    totp_secret TEXT,
    totp_pending TEXT,
    totp_last_step INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    ip TEXT
);
CREATE TABLE IF NOT EXISTS service_meta (
    service_id TEXT PRIMARY KEY,
    type_override TEXT,
    display_name TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    service_id TEXT,
    action TEXT NOT NULL,
    command TEXT,
    ok INTEGER NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_service ON audit_log(service_id, id);
CREATE TABLE IF NOT EXISTS apps (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    installation_id INTEGER,
    domain TEXT,
    type TEXT NOT NULL,
    port INTEGER,
    mode TEXT NOT NULL DEFAULT 'managed',      -- managed | adopted
    service_id TEXT,                           -- adopted: detected service id
    path TEXT,                                 -- adopted: existing project folder
    root_dir TEXT NOT NULL DEFAULT '',         -- static/php: served sub-folder
    build_command TEXT,
    start_command TEXT,
    env TEXT,                                  -- encrypted (mas.envcrypt)
    auto_deploy INTEGER NOT NULL DEFAULT 1,
    environment TEXT NOT NULL DEFAULT 'production',  -- previews later
    current_release INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    sha TEXT,
    message TEXT,
    author TEXT,
    trigger TEXT NOT NULL,                     -- push | manual | rollback | create
    actor TEXT,
    status TEXT NOT NULL,                      -- queued building live failed superseded
    path TEXT,
    log_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS releases_app ON releases(app_id, id);
CREATE TABLE IF NOT EXISTS supabase (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                      -- installing | running | stopped | failed
    domain TEXT,
    dir TEXT NOT NULL,
    ports TEXT,                                -- JSON {kong_http, kong_https, postgres, pooler}
    secrets TEXT,                              -- encrypted (mas.envcrypt)
    ref TEXT,                                  -- commit of the official docker/ folder
    error TEXT,
    last_backup TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_access (       -- CMS: which editor may edit which app
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, app_id)
);
CREATE TABLE IF NOT EXISTS invites (            -- one-time sign-up links for CMS editors
    id INTEGER PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,            -- SHA-256 of the token in the link
    apps TEXT NOT NULL DEFAULT '[]',            -- JSON: sites the new account gets
    note TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at TEXT,
    used_by TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""

MIGRATIONS = [
    ("supabase", "components TEXT"),  # JSON list of enabled optional components
    ("users", "role TEXT NOT NULL DEFAULT 'admin'"),  # admin | editor (CMS only)
    ("apps", "extra_ports TEXT"),  # load balancing: JSON ports of instances 2..N on this server
    ("apps", "backends TEXT"),     # load balancing: JSON ["host:port"] of servers outside Mas
]
ROLES = ("admin", "editor")

APP_FIELDS = {"name", "repo", "branch", "installation_id", "domain", "type", "port", "mode", "service_id",
              "path", "root_dir", "build_command", "start_command", "env", "auto_deploy", "environment",
              "current_release", "extra_ports", "backends"}
RELEASE_FIELDS = {"sha", "message", "author", "status", "path", "log_path", "error", "finished_at"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        parent = Path(self.path).parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.tx() as conn:
            conn.executescript(SCHEMA)
            for table, column in MIGRATIONS:  # columns added after the first release
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass  # already there
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn

    # -- users -----------------------------------------------------------

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self.tx() as c:
            return c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def get_user_by_id(self, user_id: int) -> sqlite3.Row | None:
        with self.tx() as c:
            return c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        with self.tx() as c:
            return c.execute("SELECT * FROM users ORDER BY id").fetchall()

    def create_user(self, username: str, password_hash: str, role: str = "admin") -> int:
        if role not in ROLES:
            raise ValueError(f"invalid role {role}")
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, _now()),
            )
            return int(cur.lastrowid)

    def delete_user(self, username: str) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0

    def update_user(self, user_id: int, **fields: Any) -> None:
        allowed = {"password_hash", "totp_secret", "totp_pending", "totp_last_step"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(f"invalid user fields: {set(fields) - allowed}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))

    def bump_totp_step(self, user_id: int, step: int) -> bool:
        """Record the last accepted TOTP step; False if it was already used (replay)."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
                (step, user_id, step),
            )
            return cur.rowcount == 1

    # -- sessions --------------------------------------------------------

    def create_session(self, token_hash: str, user_id: int, csrf: str, ttl: float, ip: str | None) -> None:
        now = time.time()
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            c.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (token_hash, user_id, csrf, now, now + ttl, now, ip),
            )

    def get_session(self, token_hash: str) -> sqlite3.Row | None:
        with self.tx() as c:
            return c.execute(
                "SELECT s.*, u.username, u.role, u.totp_secret IS NOT NULL AS totp_enabled "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()

    def touch_session(self, token_hash: str, expires_at: float) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                      (time.time(), expires_at, token_hash))

    def delete_session(self, token_hash: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep: str | None = None) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash IS NOT ?", (user_id, keep))

    # -- CMS access ------------------------------------------------------

    def user_apps(self, user_id: int) -> list[str]:
        with self.tx() as c:
            return [r[0] for r in c.execute("SELECT app_id FROM app_access WHERE user_id = ? ORDER BY app_id",
                                            (user_id,))]

    def app_editors(self, app_id: str) -> list[int]:
        with self.tx() as c:
            return [r[0] for r in c.execute("SELECT user_id FROM app_access WHERE app_id = ?", (app_id,))]

    def set_user_apps(self, user_id: int, app_ids: list[str]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM app_access WHERE user_id = ?", (user_id,))
            c.executemany("INSERT INTO app_access VALUES (?, ?, ?)", [(user_id, a, _now()) for a in set(app_ids)])

    def set_app_editors(self, app_id: str, user_ids: list[int]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM app_access WHERE app_id = ?", (app_id,))
            c.executemany("INSERT INTO app_access VALUES (?, ?, ?)", [(u, app_id, _now()) for u in set(user_ids)])

    def can_edit(self, user_id: int, role: str, app_id: str) -> bool:
        if role == "admin":
            return True
        with self.tx() as c:
            return c.execute("SELECT 1 FROM app_access WHERE user_id = ? AND app_id = ?",
                             (user_id, app_id)).fetchone() is not None

    def delete_user_by_id(self, user_id: int) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0

    # -- invites -----------------------------------------------------------

    def create_invite(self, token_hash: str, apps: list[str], note: str | None, created_by: str,
                      expires_at: float) -> int:
        with self.tx() as c:
            cur = c.execute("INSERT INTO invites (token_hash, apps, note, created_by, created_at, expires_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (token_hash, json.dumps(apps), note, created_by, _now(), expires_at))
            return int(cur.lastrowid)

    def pending_invites(self) -> list[dict]:
        with self.tx() as c:
            rows = c.execute("SELECT * FROM invites WHERE used_at IS NULL AND expires_at > ? ORDER BY id DESC",
                             (time.time(),)).fetchall()
        return [{**dict(r), "apps": json.loads(r["apps"])} for r in rows]

    def valid_invite(self, token_hash: str) -> dict | None:
        with self.tx() as c:
            row = c.execute("SELECT * FROM invites WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                            (token_hash, time.time())).fetchone()
        return {**dict(row), "apps": json.loads(row["apps"])} if row else None

    def consume_invite(self, token_hash: str, username: str) -> dict | None:
        """Mark the invite used, once: None if it was already used, revoked or expired."""
        invite = self.valid_invite(token_hash)
        if invite is None:
            return None
        with self.tx() as c:
            done = c.execute("UPDATE invites SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL",
                             (_now(), username, invite["id"])).rowcount
        return invite if done else None

    def delete_invite(self, invite_id: int) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (invite_id,)).rowcount > 0

    # -- service metadata ------------------------------------------------

    def all_meta(self) -> dict[str, sqlite3.Row]:
        with self.tx() as c:
            return {r["service_id"]: r for r in c.execute("SELECT * FROM service_meta")}

    def set_meta(self, service_id: str, **fields: str | None) -> None:
        allowed = {"type_override", "display_name", "notes"}
        if not set(fields) <= allowed:
            raise ValueError(f"invalid meta fields: {set(fields) - allowed}")
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO service_meta (service_id, updated_at) VALUES (?, ?)",
                      (service_id, _now()))
            for key, value in fields.items():
                c.execute(f"UPDATE service_meta SET {key} = ?, updated_at = ? WHERE service_id = ?",
                          (value, _now(), service_id))

    # -- audit log -------------------------------------------------------

    def audit(self, actor: str, action: str, ok: bool, service_id: str | None = None,
              command: list[str] | str | None = None, detail: str | None = None) -> None:
        if isinstance(command, list):
            command = json.dumps(command)
        with self.tx() as c:
            c.execute(
                "INSERT INTO audit_log (at, actor, service_id, action, command, ok, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), actor, service_id, action, command, int(ok), (detail or "")[:4000]),
            )

    # -- supabase --------------------------------------------------------

    def get_supabase(self, instance_id: str = "supabase") -> dict | None:
        with self.tx() as c:
            row = c.execute("SELECT * FROM supabase WHERE id = ?", (instance_id,)).fetchone()
        return dict(row) if row else None

    def save_supabase(self, instance_id: str, **fields: Any) -> None:
        allowed = {"status", "domain", "dir", "ports", "secrets", "ref", "error", "last_backup", "components"}
        if not set(fields) <= allowed:
            raise ValueError(f"invalid supabase fields: {set(fields) - allowed}")
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO supabase (id, status, dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                      (instance_id, fields.get("status", "installing"), fields.get("dir", ""), _now(), _now()))
            for key, value in fields.items():
                c.execute(f"UPDATE supabase SET {key} = ?, updated_at = ? WHERE id = ?", (value, _now(), instance_id))

    def delete_supabase(self, instance_id: str = "supabase") -> None:
        with self.tx() as c:
            c.execute("DELETE FROM supabase WHERE id = ?", (instance_id,))

    # -- settings (key/value; secrets are stored encrypted by the caller) --

    def get_setting(self, key: str) -> str | None:
        with self.tx() as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str | None) -> None:
        with self.tx() as c:
            if value is None:
                c.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                c.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                          (key, value, _now()))

    # -- apps & releases (phase 3) ----------------------------------------

    def create_app(self, app_id: str, **fields: Any) -> None:
        if not set(fields) <= APP_FIELDS:
            raise ValueError(f"invalid app fields: {set(fields) - APP_FIELDS}")
        cols = ["id", "created_at", "updated_at", *fields]
        with self.tx() as c:
            c.execute(f"INSERT INTO apps ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      (app_id, _now(), _now(), *fields.values()))

    def get_app(self, app_id: str) -> dict | None:
        with self.tx() as c:
            row = c.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        return dict(row) if row else None

    def list_apps(self) -> list[dict]:
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM apps ORDER BY id")]

    def apps_for_push(self, repo: str, branch: str) -> list[dict]:
        with self.tx() as c:
            rows = c.execute(
                "SELECT * FROM apps WHERE lower(repo) = lower(?) AND branch = ? AND auto_deploy = 1",
                (repo, branch)).fetchall()
        return [dict(r) for r in rows]

    def update_app(self, app_id: str, **fields: Any) -> None:
        if not fields or not set(fields) <= APP_FIELDS:
            raise ValueError(f"invalid app fields: {set(fields) - APP_FIELDS}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE apps SET {cols}, updated_at = ? WHERE id = ?", (*fields.values(), _now(), app_id))

    def delete_app(self, app_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM apps WHERE id = ?", (app_id,))

    def create_release(self, app_id: str, trigger: str, actor: str | None, sha: str | None = None,
                       status: str = "queued") -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO releases (app_id, sha, trigger, actor, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (app_id, sha, trigger, actor, status, _now()))
            return int(cur.lastrowid)

    def update_release(self, release_id: int, **fields: Any) -> None:
        if not fields or not set(fields) <= RELEASE_FIELDS:
            raise ValueError(f"invalid release fields: {set(fields) - RELEASE_FIELDS}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE releases SET {cols} WHERE id = ?", (*fields.values(), release_id))

    def get_release(self, release_id: int) -> dict | None:
        with self.tx() as c:
            row = c.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
        return dict(row) if row else None

    def list_releases(self, app_id: str, limit: int = 50) -> list[dict]:
        with self.tx() as c:
            rows = c.execute("SELECT * FROM releases WHERE app_id = ? ORDER BY id DESC LIMIT ?",
                             (app_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def history(self, service_id: str | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM audit_log", []
        if service_id is not None:
            sql, args = sql + " WHERE service_id = ?", [service_id]
        with self.tx() as c:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) | {"ok": bool(r["ok"])} for r in rows]
