"""HTML body of the e-mails Mas sends (alerts, reports). Table layout and inline styles only, so mail clients
render it; every value is escaped (logs, error messages and domains are untrusted text). The plain text
version stays the first part of the message for clients that do not show HTML."""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit

TONES = {  # accent, soft background, label
    "alert": ("#dc2626", "#fef2f2", "Needs attention"),
    "warning": ("#d97706", "#fffbeb", "Warning"),
    "ok": ("#16a34a", "#f0fdf4", "Back to normal"),
    "info": ("#4f46e5", "#eef2ff", "Information"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
FACT_RE = re.compile(r"^([A-Z][^:\n]{1,32}): (.+)$")


def parse_text(text: str) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Plain text lines -> (paragraphs, "Key: value" rows, bullet items)."""
    paragraphs: list[str] = []
    facts: list[tuple[str, str]] = []
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            items.append(line[2:])
        elif (m := FACT_RE.match(line)) and not line.endswith("."):
            facts.append((m.group(1), m.group(2)))
        else:
            paragraphs.append(line)
    return paragraphs, facts, items


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.hostname else None


def render(*, title: str, level: str = "alert", text: str = "", facts: list[tuple[str, str]] | None = None,
           problems: list[tuple[str, str, str]] | None = None, pre: str = "", pre_title: str = "",
           button: tuple[str, str] | None = None, footer: str = "") -> str:
    """``problems``: (title, detail, hint) rows, each with its own card; ``pre``: a block of log lines."""
    accent, soft, label = TONES.get(level, TONES["info"])
    paragraphs, parsed_facts, items = parse_text(text)
    rows = [*parsed_facts, *(facts or [])]
    out = [
        "<!doctype html><html><body style=\"margin:0;padding:0;background:#f4f4f5\">",
        f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#f4f4f5;padding:24px 12px;font-family:{FONT}\"><tr><td align=\"center\">",
        "<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e4e4e7\">",
        f"<tr><td style=\"height:5px;background:{accent};font-size:0;line-height:0\">&nbsp;</td></tr>",
        "<tr><td style=\"padding:22px 28px 6px\">",
        f"<div style=\"font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:{accent}\">{escape(label)}</div>",
        f"<h1 style=\"margin:6px 0 0;font-size:21px;line-height:1.3;color:#18181b\">{escape(title)}</h1></td></tr>",
    ]
    if paragraphs:
        body = "".join(f"<p style=\"margin:0 0 10px;font-size:14px;line-height:1.55;color:#3f3f46\">{escape(p)}</p>" for p in paragraphs)
        out.append(f"<tr><td style=\"padding:10px 28px 0\">{body}</td></tr>")
    if problems:
        cards = []
        for name, detail, hint in problems:
            extra = f"<div style=\"margin-top:6px;font-size:12px;color:#52525b\"><b>What to do:</b> {escape(hint)}</div>" if hint else ""
            cards.append(
                f"<div style=\"margin:0 0 8px;padding:10px 12px;border-left:3px solid {accent};background:{soft};border-radius:6px\">"
                f"<div style=\"font-size:14px;font-weight:600;color:#18181b\">{escape(name)}</div>"
                f"<div style=\"margin-top:2px;font-size:13px;color:#3f3f46\">{escape(detail)}</div>{extra}</div>")
        out.append(f"<tr><td style=\"padding:8px 28px 0\">{''.join(cards)}</td></tr>")
    if items:
        lis = "".join(f"<li style=\"margin:0 0 4px\">{escape(i)}</li>" for i in items)
        out.append(f"<tr><td style=\"padding:4px 28px 0\"><ul style=\"margin:0;padding-left:20px;font-size:14px;line-height:1.5;color:#3f3f46\">{lis}</ul></td></tr>")
    if rows:
        trs = "".join(
            f"<tr><td style=\"width:130px;padding:6px 12px 6px 0;font-size:12px;color:#71717a;white-space:nowrap;vertical-align:top\">{escape(k)}</td>"
            f"<td style=\"padding:6px 0;font-size:13px;color:#18181b;word-break:break-word\">{escape(v)}</td></tr>" for k, v in rows)
        out.append(f"<tr><td style=\"padding:14px 28px 0\"><table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%;border-top:1px solid #e4e4e7\">{trs}</table></td></tr>")
    if pre.strip():
        head = f"<div style=\"font-size:12px;font-weight:600;color:#52525b;margin-bottom:6px\">{escape(pre_title)}</div>" if pre_title else ""
        out.append(f"<tr><td style=\"padding:14px 28px 0\">{head}<pre style=\"margin:0;padding:12px;background:#18181b;color:#e4e4e7;border-radius:8px;font-size:11.5px;line-height:1.5;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Menlo,Consolas,monospace\">{escape(pre.strip())}</pre></td></tr>")
    url = _safe_url(button[1]) if button else None
    if button and url:
        out.append(f"<tr><td style=\"padding:20px 28px 0\"><a href=\"{escape(url, quote=True)}\" style=\"display:inline-block;padding:10px 18px;background:{accent};color:#ffffff;text-decoration:none;border-radius:8px;font-size:14px;font-weight:600\">{escape(button[0])}</a></td></tr>")
    out.append(f"<tr><td style=\"padding:24px 28px 22px;font-size:11.5px;line-height:1.5;color:#a1a1aa\">{escape(footer or 'Sent by Mas, your server dashboard.')}</td></tr>")
    out.append("</table></td></tr></table></body></html>")
    return "".join(out)
