"""Environment variables an app expects, read from its ``.env.example`` / ``.env.sample``.

The file is only parsed as text: nothing from the repository is ever executed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

ENV_EXAMPLE_FILES = (".env.example", ".env.sample")
MAX_ENV_EXAMPLE_BYTES = 64 * 1024

# Injected by Mas in the systemd unit: never asked to the user.
RESERVED_KEYS = frozenset({"PORT", "HOST", "HOSTNAME", "NODE_ENV"})
SECRET_MARKERS = ("PASS", "TOKEN", "SECRET", "KEY")

_ACTIVE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
# A commented line only counts as a variable when its key looks like one (``# FOO_BAR=1``),
# so that prose such as ``# e.g. a=b`` is kept as a description.
_COMMENTED_RE = re.compile(r"^#+\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=(.*)$")
_PLACEHOLDER_RE = re.compile(
    r"^(?:<.*>|\[.*\]|\$\{.*\}|change[-_ ]?me.*|replace[-_ ]?me.*|your[-_ ].*|xxx+|\.\.\.|…|todo|tbd|"
    r"secret|password)$",
    re.IGNORECASE,
)


@dataclass
class EnvVar:
    key: str
    value: str          # prefilled value ("" when the user must type it, or for an optional variable)
    example: str        # value found in the file (shown as a hint)
    optional: bool      # was commented out in the file
    required: bool      # not optional and no usable value: creation is blocked until it is filled
    secret: bool        # masked field
    description: str


def is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def is_placeholder(value: str) -> bool:
    value = value.strip()
    return not value or bool(_PLACEHOLDER_RE.fullmatch(value))


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else raw[1:]
    # Unquoted: ``value # comment`` ends at the comment.
    match = re.search(r"\s#", raw)
    return (raw[:match.start()] if match else raw).strip()


def parse_env_example(text: str) -> tuple[list[EnvVar], list[str]]:
    """(variables in file order, reserved keys that were skipped).

    - ``KEY=value`` is a variable; ``# KEY=value`` an optional one.
    - Comment lines right above a variable (no blank line between) describe it.
    - Quotes around a value are removed; blank lines are ignored.
    - A key defined twice keeps its first position; an active definition beats a
      commented one, and among active definitions the last wins (dotenv behaviour).
    """
    found: dict[str, dict] = {}
    ignored: list[str] = []
    pending: list[str] = []
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line:
            pending = []
            continue
        active = _ACTIVE_RE.match(line)
        commented = None if active or not line.startswith("#") else _COMMENTED_RE.match(line)
        match = active or commented
        if not match:
            if line.startswith("#"):
                comment = line.lstrip("#").strip()
                if comment and not set(comment) <= set("-=_*~#"):  # skip ruler lines
                    pending.append(comment)
            continue
        key, value = match.group(1), _unquote(match.group(2))
        description = " ".join(pending)
        pending = []
        if key.upper() in RESERVED_KEYS:
            if key not in ignored:
                ignored.append(key)
            continue
        entry = found.get(key)
        if entry is None:
            found[key] = {"value": value, "optional": commented is not None, "description": description}
        elif active:
            entry["value"], entry["optional"] = value, False
            entry["description"] = description or entry["description"]
        else:  # a commented duplicate never overrides anything
            entry["description"] = entry["description"] or description
    variables = []
    for key, entry in found.items():
        needs_input = is_placeholder(entry["value"])
        optional = entry["optional"]
        variables.append(EnvVar(
            key=key,
            value="" if (optional or needs_input) else entry["value"],
            example=entry["value"],
            optional=optional,
            required=not optional and needs_input,
            secret=is_secret_key(key),
            description=entry["description"],
        ))
    return variables, ignored


def detect_env(content: bytes | None) -> dict:
    """JSON payload for the new-app form from the raw bytes of an env example file."""
    if not content:
        return {"env": [], "env_ignored": []}
    variables, ignored = parse_env_example(content[:MAX_ENV_EXAMPLE_BYTES].decode("utf-8", errors="replace"))
    return {"env": [asdict(v) for v in variables], "env_ignored": ignored}
