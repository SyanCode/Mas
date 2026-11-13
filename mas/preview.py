"""Private preview of an entry: a link anyone can open for a few days, without an account.

The page shows the content as the CMS holds it (a draft included), not the site's own design: it is for
reading and approving the words and the images. A link is signed (HMAC) and names one entry; it stops working
when it expires or when the administrator revokes the links of the site."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from html import escape
from urllib.parse import quote

MAX_DAYS = 30
SECRET_KEY = "preview_secret"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def secret(db) -> bytes:
    value = db.get_setting(SECRET_KEY)
    if not value:
        value = secrets.token_hex(32)
        db.set_setting(SECRET_KEY, value)
    return value.encode()


def version(db, app_id: str) -> int:
    return int(db.get_setting(f"preview_v:{app_id}") or 0)


def revoke(db, app_id: str) -> None:
    db.set_setting(f"preview_v:{app_id}", str(version(db, app_id) + 1))


def make(db, app_id: str, cid: str, slug: str, days: int, now: float | None = None) -> tuple[str, float]:
    expires = (now if now is not None else time.time()) + max(1, min(days, MAX_DAYS)) * 86400
    body = _b64(json.dumps({"a": app_id, "c": cid, "s": slug, "e": int(expires), "v": version(db, app_id)},
                           separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret(db), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", expires


def check(db, token: str, now: float | None = None) -> dict | None:
    """The entry a link names, or None (forged, expired or revoked)."""
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(secret(db), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(body))
        if data["e"] < (now if now is not None else time.time()) or data["v"] != version(db, data["a"]):
            return None
        return {"app_id": data["a"], "cid": data["c"], "slug": data["s"], "expires": data["e"]}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


# -- the page ------------------------------------------------------------------------------------

INLINE = [(re.compile(r"`([^`]+)`"), r"<code>\1</code>"), (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
          (re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])"), r"<em>\1</em>")]
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]*)\)")


def inline(text: str) -> str:
    out = escape(text, quote=True)
    out = LINK.sub(lambda m: f'<a href="{m.group(2)}" rel="noopener noreferrer nofollow">{m.group(1)}</a>', out)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


def markdown(text: str, image: str = "") -> str:
    """A small, safe subset: headings, paragraphs, lists, quotes, code, bold, italic, links. Everything is escaped first."""
    blocks, para, items, code = [], [], [], None
    kind = ""

    def flush() -> None:
        nonlocal para, items, kind
        if para:
            blocks.append("<p>" + "<br>".join(inline(l) for l in para) + "</p>")
        if items:
            blocks.append(f"<{kind}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{kind}>")
        para, items, kind = [], [], ""

    for line in text.replace("\r\n", "\n").split("\n"):
        if code is not None:
            if line.strip().startswith("```"):
                blocks.append("<pre><code>" + escape("\n".join(code)) + "</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush(); code = []
        elif not s:
            flush()
        elif m := re.match(r"^(#{1,4})\s+(.*)$", s):
            flush(); n = len(m.group(1)) + 1
            blocks.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif m := re.match(r"^[-*]\s+(.*)$", s):
            if kind != "ul":
                flush(); kind = "ul"
            items.append(m.group(1))
        elif m := re.match(r"^\d+[.)]\s+(.*)$", s):
            if kind != "ol":
                flush(); kind = "ol"
            items.append(m.group(1))
        elif s.startswith(">"):
            flush(); blocks.append(f"<blockquote>{inline(s.lstrip('> '))}</blockquote>")
        elif m := re.match(r"^!\[([^\]]*)\]\((/[^\s)]+)\)$", s):
            flush(); blocks.append(f'<img src="{image}{quote(m.group(2), safe="/")}" alt="{escape(m.group(1), quote=True)}">')
        else:
            if items:
                flush()
            para.append(s)
    if code is not None:
        blocks.append("<pre><code>" + escape("\n".join(code)) + "</code></pre>")
    flush()
    return "\n".join(blocks)


def _value(field: dict, value, image_url: str) -> str:
    kind = field["type"]
    if value in (None, "", [], False) and kind != "boolean":
        return '<span class="empty">—</span>'
    if kind == "markdown":
        return f'<div class="md">{markdown(str(value), image_url)}</div>'
    if kind == "image":
        return f'<img src="{image_url}{quote(str(value), safe="/")}" alt="">'
    if kind == "boolean":
        return "Yes" if value else "No"
    if kind == "color":
        return f'<span class="chip" style="background:{escape(str(value), quote=True) if re.fullmatch(r"#[0-9a-fA-F]{3,8}", str(value)) else "none"}"></span> {escape(str(value))}'
    if kind == "list" and isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, dict):
                rows.append("<li>" + " · ".join(escape(str(v)) for v in item.values() if v not in (None, "")) + "</li>")
            else:
                rows.append(f"<li>{escape(str(item))}</li>")
        return "<ul>" + "".join(rows) + "</ul>"
    return escape(str(value)).replace("\n", "<br>")


STYLE = """
:root{color-scheme:light dark}*{box-sizing:border-box}
body{margin:0;font:16px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;background:#f6f5f1;color:#17171a}
@media(prefers-color-scheme:dark){body{background:#0c0c0d;color:#ececf0}.bar{background:#17171a;border-color:#2a2a30}.field{border-color:#2a2a30}.label{color:#9a9aa5}code,pre{background:#17171a}}
.bar{position:sticky;top:0;background:#fff;border-bottom:1px solid #e6e3da;padding:10px 20px;font-size:13px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.pill{background:#5b5bd6;color:#fff;border-radius:99px;padding:2px 10px;font-size:12px;font-weight:600}
main{max-width:720px;margin:0 auto;padding:40px 20px 80px}
h1{font:400 40px/1.1 "Instrument Serif",Georgia,serif;margin:0 0 6px}h2,h3,h4,h5{margin:1.6em 0 .4em}
.sub{color:#77777f;font-size:14px;margin-bottom:32px}.field{border-top:1px solid #e6e3da;padding:18px 0}
.label{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#77777f;margin-bottom:6px}
img{max-width:100%;border-radius:12px}pre,code{background:#eceae3;border-radius:6px;padding:2px 5px;font-size:14px}pre{padding:12px;overflow:auto}
blockquote{margin:1em 0;padding-left:14px;border-left:3px solid #5b5bd6;color:#555}.empty{opacity:.4}.chip{display:inline-block;width:14px;height:14px;border-radius:4px;vertical-align:-2px}
a{color:#5b5bd6}
"""


def page(site_name: str, collection: dict, entry: dict, token: str, expires: float) -> str:
    image_url = f"/preview/{token}/image?src="
    title_key = collection.get("title_field")
    title = str(entry["values"].get(title_key) or entry["slug"]) if title_key else entry["slug"]
    status = entry.get("status") or "published"
    rows = []
    for f in collection["fields"]:
        if f["key"] == title_key:
            continue
        rows.append(f'<div class="field"><div class="label">{escape(f["label"])}</div>{_value(f, entry["values"].get(f["key"]), image_url)}</div>')
    until = time.strftime("%Y-%m-%d", time.gmtime(expires))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>Preview: {escape(title)}</title><style>{STYLE}</style></head><body>'
            f'<div class="bar"><span class="pill">Preview</span><span>{escape(site_name)} · {escape(collection["item_label"])}'
            f'{" · not published yet" if status != "published" else ""}</span><span style="margin-left:auto;opacity:.6">Link valid until {until}</span></div>'
            f'<main><h1>{escape(title)}</h1><div class="sub">{escape(collection["label"])} / {escape(entry["slug"])}</div>{"".join(rows)}</main></body></html>')
