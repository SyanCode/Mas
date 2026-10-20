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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        parent = Path(self.path).parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.tx() as conn:
            conn.executescript(SCHEMA)
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

    def create_user(self, username: str, password_hash: str) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, _now()),
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
                "SELECT s.*, u.username, u.totp_secret IS NOT NULL AS totp_enabled "
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

    def history(self, service_id: str | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM audit_log", []
        if service_id is not None:
            sql, args = sql + " WHERE service_id = ?", [service_id]
        with self.tx() as c:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) | {"ok": bool(r["ok"])} for r in rows]
