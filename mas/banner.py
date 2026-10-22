"""The Mas logo in the terminal (``mas --version``, the installer, ``mas self-update``).

Also runs alone, before Mas is installed: ``python3 mas/banner.py [version]``.
"""

from __future__ import annotations

import os
import sys

ART = """\
          ▄▄▄▄
       ▄███▀▀███▄
   ▄▄██▀▀      ▀▀██▄▄         ▄▄▄ ▄▄▄   ▄▄▄     ▄▄▄▄▄▄    ▄▄▄▄▄▄
 ▄██▀              ▀██▄       ████▀█████▀███▄  ███▀▀███  ███▀▀███
 ██   ██▄      ▄██   ██       ███   ███   ███   ▄▄▄▄███  ███▄▄▄▄
 ██   ████▄  ▄████   ██       ███   ███   ███ ▄██▀▀▀███   ▀▀▀▀███▄
 ██   ██ ▀████▀ ██   ██       ███   ███   ███ ▀██▄▄▄███  ███▄▄▄██▀
 ██   ██        ██   ██       ▀▀▀   ▀▀▀   ▀▀▀  ▀▀▀▀▀▀▀▀   ▀▀▀▀▀▀
 ▀██▄  ▀        ▀▀ ▄██▀
   ▀▀██▄▄      ▄▄██▀▀
      ▀▀███▄▄███▀▀
          ▀▀▀▀"""
LOGO_WIDTH = 23          # columns of the hexagon; the rest is the word "mas"
GRADIENT = ((129, 140, 248), (56, 189, 248), (232, 121, 249))  # indigo-400, sky-400, fuchsia-400, like the site
TEXT = (244, 244, 245)
TAGLINE = "Your server, mapped and deployed."


def _mix(t: float) -> tuple[int, int, int]:
    a, b = (GRADIENT[0], GRADIENT[1]) if t < 0.5 else (GRADIENT[1], GRADIENT[2])
    u = t * 2 if t < 0.5 else (t - 0.5) * 2
    return tuple(round(x + (y - x) * u) for x, y in zip(a, b))


def colors(stream=None) -> bool:
    stream = stream or sys.stdout
    return (hasattr(stream, "isatty") and stream.isatty() and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM") != "dumb")


def render(version: str | None = None, color: bool = True, subtitle: str | None = None) -> str:
    lines = ART.splitlines()
    out = []
    for row, line in enumerate(lines):
        if not color:
            out.append(line)
            continue
        parts = []
        for col, ch in enumerate(line):
            if ch == " ":
                parts.append(ch)
                continue
            if col < LOGO_WIDTH:  # diagonal gradient over the hexagon
                r, g, b = _mix(min(1.0, (col / LOGO_WIDTH + row / len(lines)) / 2))
            else:
                r, g, b = TEXT
            parts.append(f"\033[38;2;{r};{g};{b}m{ch}")
        out.append("".join(parts) + "\033[0m")
    info = " · ".join(x for x in (f"v{version}" if version else None, subtitle or TAGLINE) if x)
    out += ["", f"  \033[2m{info}\033[0m" if color else f"  {info}"]
    return "\n" + "\n".join(out) + "\n"


def version_from_source(src: str | None = None) -> str | None:
    """The version written in the source (after an update, the running process still has the old one)."""
    path = os.path.join(src, "mas", "__init__.py") if src else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
    try:
        for line in open(path, encoding="utf-8"):
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    print(render(args[0] if args else version_from_source(), colors(), " ".join(args[1:]) or None))
