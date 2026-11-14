"""Profile of an account: full name, nickname, a short presentation and a picture.

The picture is a small raster image (PNG, JPEG or WebP, detected from its first bytes, never SVG or
anything that could carry a script) sent as a data URL, at most 300 KB: the dashboard shrinks it to
256 x 256 in the browser before sending. It is served with ``nosniff`` and a sandboxing CSP, only to
the account itself and to administrators.
"""

from __future__ import annotations

import base64
import binascii
import re

NAME_MAX, NICK_MAX, BIO_MAX = 80, 32, 300
AVATAR_MAX = 300 * 1024
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")  # no control or bidi-override characters
NICK_RE = re.compile(r"^[\w][\w .'’-]*$", re.UNICODE)
DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$")


class ProfileError(Exception):
    pass


def clean(value: object, label: str, limit: int, multiline: bool = False) -> str | None:
    """A stripped text, ``None`` when empty."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(f"{label}: text expected")
    text = value.strip()
    if not multiline:
        text = " ".join(text.split())
    if len(text) > limit:
        raise ProfileError(f"{label}: at most {limit} characters")
    if CONTROL.search(text) or (not multiline and "\n" in text):
        raise ProfileError(f"{label}: control characters are not allowed")
    return text or None


def clean_profile(body: dict) -> dict:
    """The validated fields of a profile form (only the keys present)."""
    out: dict = {}
    if "display_name" in body:
        out["display_name"] = clean(body["display_name"], "full name", NAME_MAX)
    if "nickname" in body:
        nick = clean(body["nickname"], "nickname", NICK_MAX)
        if nick and not NICK_RE.fullmatch(nick):
            raise ProfileError("nickname: letters, digits, spaces and . _ ' -")
        out["nickname"] = nick
    if "bio" in body:
        out["bio"] = clean(body["bio"], "about", BIO_MAX, multiline=True)
    return out


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def parse_avatar(data_url: object) -> tuple[bytes, str]:
    """The bytes and type of a data URL, checked against the real content."""
    if not isinstance(data_url, str) or len(data_url) > AVATAR_MAX * 2:
        raise ProfileError("picture: too large")
    m = DATA_URL.fullmatch(data_url.strip())
    if not m:
        raise ProfileError("picture: a PNG, JPEG or WebP image")
    try:
        data = base64.b64decode(re.sub(r"\s", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise ProfileError("picture: not a valid image") from None
    if not data or len(data) > AVATAR_MAX:
        raise ProfileError(f"picture: at most {AVATAR_MAX // 1024} KB")
    kind = sniff(data)
    if kind is None or kind != m.group(1):
        raise ProfileError("picture: a PNG, JPEG or WebP image")
    return data, kind
