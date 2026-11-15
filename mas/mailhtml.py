"""HTML body of the e-mails Mas sends (alerts, reports). Table layout and inline styles only, so mail clients
render it; every value is escaped (logs, error messages and domains are untrusted text). The plain text
version stays the first part of the message for clients that do not show HTML."""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit

TONES = {  # status dot, label (Studio keeps colour for the one thing that matters)
    "alert": ("#d9483b", "Needs attention"),
    "warning": ("#c98a1b", "Warning"),
    "ok": ("#2f9e6f", "Back to normal"),
    "info": ("#5b5bd6", "Information"),
}
SANS = "'Geist','Helvetica Neue',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"
SERIF = "'Instrument Serif','Iowan Old Style','Palatino Linotype',Georgia,serif"
MONO = "'Geist Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
FACT_RE = re.compile(r"^([A-Z][^:\n]{1,32}): (.+)$")
PAPER, CARD, HAIR, INK, SOFT, MUTED, INDIGO, WELL = "#f6f5f1", "#ffffff", "#e6e3da", "#17171a", "#3d3b35", "#77746b", "#5b5bd6", "#f4f2ec"


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


def _row(inner: str, pad: str = "0 40px") -> str:
    return f"<tr><td style=\"padding:{pad}\">{inner}</td></tr>"


def render(*, title: str, level: str = "alert", text: str = "", facts: list[tuple[str, str]] | None = None,
           tiles: list[tuple[str, str]] | None = None, problems: list[tuple[str, str, str]] | None = None,
           pre: str = "", pre_title: str = "", button: tuple[str, str] | None = None, footer: str = "",
           context: str = "") -> str:
    """The Studio look of the dashboard: warm paper, a white card, ink, a serif title, light figures, hairlines,
    one dot of colour for the status. ``facts``: label/value rows, ``tiles``: up to four figures,
    ``problems``: (title, detail, hint), ``pre``: log lines, ``context``: small text at the top right.
    Every value is escaped."""
    dot_color, label = TONES.get(level, TONES["info"])
    paragraphs, parsed_facts, items = parse_text(text)
    rows = [*parsed_facts, *(facts or [])]
    out = [
        f"<!doctype html><html><head><meta name=\"color-scheme\" content=\"light\"><meta name=\"supported-color-schemes\" content=\"light\"></head>"
        f"<body style=\"margin:0;padding:0;background:{PAPER}\" bgcolor=\"{PAPER}\">",
        f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" bgcolor=\"{PAPER}\" style=\"background:{PAPER};padding:36px 12px;font-family:{SANS}\"><tr><td align=\"center\">",
        # brand line, outside the card, like the sidebar of the dashboard
        f"<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:600px;width:100%\"><tr>"
        f"<td style=\"padding:0 6px 14px;font-size:17px;font-weight:600;letter-spacing:-.02em;color:{INK}\"><span style=\"color:{INDIGO}\">&#11041;</span>&nbsp; mas</td>"
        f"<td align=\"right\" style=\"padding:0 6px 14px;font-size:11.5px;color:{MUTED};font-family:{MONO}\">{escape(context)}</td></tr></table>",
        f"<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" bgcolor=\"{CARD}\" style=\"max-width:600px;width:100%;background:{CARD};border-radius:18px;border:1px solid {HAIR};box-shadow:0 1px 2px rgba(0,0,0,.04),0 18px 36px -22px rgba(0,0,0,.16)\">",
        # eyebrow + serif title
        _row(f"<div style=\"font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:{MUTED}\">"
             f"<span style=\"display:inline-block;width:7px;height:7px;border-radius:4px;background:{dot_color};margin-right:8px\"></span>{escape(label)}</div>"
             f"<h1 style=\"margin:14px 0 0;font-family:{SERIF};font-size:38px;line-height:1.08;letter-spacing:-.02em;font-weight:400;color:{INK}\">{escape(title)}</h1>", "40px 40px 0"),
    ]
    if paragraphs:
        body = "".join(f"<p style=\"margin:0 0 10px;font-size:15px;line-height:1.65;color:{SOFT}\">{escape(p)}</p>" for p in paragraphs)
        out.append(_row(body, "16px 40px 0"))
    if tiles:
        shown = tiles[:4]
        cells = "".join(
            f"<td valign=\"top\" style=\"padding:16px {14 if i < len(shown) - 1 else 0}px 16px {0 if i == 0 else 18}px;{'' if i == 0 else f'border-left:1px solid {HAIR};'}\">"
            f"<div style=\"font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:{MUTED}\">{escape(k)}</div>"
            f"<div style=\"margin-top:6px;font-size:28px;font-weight:300;letter-spacing:-.035em;line-height:1.1;color:{INK};word-break:break-word\">{escape(v)}</div></td>"
            for i, (k, v) in enumerate(shown))
        out.append(_row(f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-top:1px solid {HAIR};border-bottom:1px solid {HAIR}\"><tr>"
                        f"{cells}</tr></table>", "26px 40px 0"))
    if problems:
        entries = []
        for name, detail, hint in problems:
            extra = f"<div style=\"margin-top:8px;font-size:13px;line-height:1.55;color:{INDIGO}\">&rarr;&nbsp; {escape(hint)}</div>" if hint else ""
            entries.append(
                f"<tr><td valign=\"top\" style=\"width:22px;padding:17px 0 0\"><span style=\"display:inline-block;width:8px;height:8px;border-radius:4px;background:{dot_color}\"></span></td>"
                f"<td style=\"padding:14px 0;border-bottom:1px solid {HAIR}\"><div style=\"font-size:15px;font-weight:600;color:{INK}\">{escape(name)}</div>"
                f"<div style=\"margin-top:3px;font-size:13.5px;line-height:1.55;color:{SOFT}\">{escape(detail)}</div>{extra}</td></tr>")
        out.append(_row(f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-top:1px solid {HAIR}\">{''.join(entries)}</table>", "22px 40px 0"))
    if items:
        lis = "".join(f"<tr><td valign=\"top\" style=\"padding:3px 12px 3px 0;color:{INDIGO};font-size:14px\">&bull;</td><td style=\"padding:3px 0;font-size:14px;line-height:1.55;color:{SOFT}\">{escape(i)}</td></tr>" for i in items)
        out.append(_row(f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\">{lis}</table>", "14px 40px 0"))
    if rows:
        trs = "".join(
            f"<tr><td style=\"width:130px;padding:10px 12px 10px 0;border-bottom:1px solid {HAIR};font-size:12.5px;color:{MUTED};white-space:nowrap;vertical-align:top\">{escape(k)}</td>"
            f"<td style=\"padding:10px 0;border-bottom:1px solid {HAIR};font-size:13px;color:{INK};word-break:break-word;font-family:{MONO}\">{escape(v)}</td></tr>" for k, v in rows)
        out.append(_row(f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%\">{trs}</table>", "20px 40px 0"))
    if pre.strip():
        head = f"<div style=\"padding:0 0 8px;font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:{MUTED}\">{escape(pre_title or 'Output')}</div>"
        out.append(_row(
            f"{head}<pre style=\"margin:0;padding:16px 18px;background:{WELL};border:1px solid {HAIR};border-radius:12px;color:{SOFT};font-size:12px;line-height:1.65;white-space:pre-wrap;word-break:break-word;font-family:{MONO}\">{escape(pre.strip())}</pre>", "24px 40px 0"))
    url = _safe_url(button[1]) if button else None
    if button and url:
        out.append(_row(
            f"<a href=\"{escape(url, quote=True)}\" style=\"display:inline-block;padding:13px 24px;background:{INK};color:#fafaf8;text-decoration:none;border-radius:10px;font-size:14px;font-weight:500\">{escape(button[0])}&nbsp;&nbsp;&rarr;</a>", "28px 40px 0"))
    out.append(_row(f"<div style=\"height:34px;font-size:0;line-height:0\">&nbsp;</div>", "0"))
    out.append("</table>")
    out.append(f"<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:600px;width:100%\"><tr><td style=\"padding:18px 6px 0;font-size:12px;line-height:1.65;color:{MUTED}\">"
               f"{escape(footer or 'Sent by Mas, your server dashboard.')}<br><span style=\"font-family:{SERIF};font-size:15px;color:#a8a69d\">Your server, mapped.</span></td></tr></table>")
    out.append("</td></tr></table></body></html>")
    return "".join(out)
