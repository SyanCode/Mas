"""Authentication: argon2 passwords, TOTP second factor, sessions, rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from mas.db import Database

MIN_PASSWORD_LENGTH = 10
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")
TOTP_ISSUER = "Mas"
TOTP_PERIOD = 30

_hasher = PasswordHasher()
# Verified when the username does not exist, so timing does not reveal it.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


class AuthError(Exception):
    """``code`` is one of: invalid, otp_required, otp_invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# -- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def check_password_strength(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    return None


# -- TOTP ----------------------------------------------------------------------


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)


def qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, border=2)


def qr_terminal(uri: str) -> None:
    segno.make(uri, error="m").terminal(compact=True)


def match_totp(secret: str, code: str, now: float | None = None) -> int | None:
    """Return the matching time step (±1 step of clock drift), or None."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    totp = pyotp.TOTP(secret)
    step = int((now or time.time()) // TOTP_PERIOD)
    for candidate in (step, step - 1, step + 1):
        if hmac.compare_digest(totp.at(candidate * TOTP_PERIOD), code):
            return candidate
    return None


def verify_totp(db: Database, user, code: str) -> bool:
    """Check a code for an enrolled user. Each code works once (replay protection)."""
    step = match_totp(user["totp_secret"], code)
    return step is not None and db.bump_totp_step(user["id"], step)


# -- login ---------------------------------------------------------------------


def authenticate(db: Database, username: str, password: str, otp: str | None):
    user = db.get_user(username)
    if user is None:
        verify_password(_DUMMY_HASH, password)
        raise AuthError("invalid")
    if not verify_password(user["password_hash"], password):
        raise AuthError("invalid")
    if user["totp_secret"]:
        if not otp:
            raise AuthError("otp_required")
        if not verify_totp(db, user, otp):
            raise AuthError("otp_invalid")
    if _hasher.check_needs_rehash(user["password_hash"]):
        db.update_user(user["id"], password_hash=hash_password(password))
    return user


# -- sessions ------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class NewSession:
    token: str
    csrf: str


def create_session(db: Database, user_id: int, ttl_seconds: float, ip: str | None) -> NewSession:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.create_session(hash_token(token), user_id, csrf, ttl_seconds, ip)
    return NewSession(token, csrf)


# -- rate limiting -------------------------------------------------------------


class RateLimiter:
    """Sliding window counter, in memory (single process)."""

    def __init__(self, max_hits: int, window_seconds: float) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.max_hits

    def hit(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
