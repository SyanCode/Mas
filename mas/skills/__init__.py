"""Skills: written know-how the robot (and any Claude connected over MCP) loads when a goal needs it.

Each skill is a Markdown file in this folder in the format of Claude Code skills (``SKILL.md``): a front matter with
``name`` and ``description``, then the instructions. The robot only sees the catalogue (names and descriptions) in its
system prompt and loads a skill with ``load_skill`` when the goal calls for it, so the prompt stays short. ``export``
writes them as ``.claude/skills/<name>/SKILL.md`` so Claude Code uses the same rules inside a project's repository.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class SkillError(Exception):
    pass


def _parse(text: str) -> tuple[dict[str, str], str]:
    m = FRONT.match(text)
    if not m:
        raise SkillError("a skill starts with a front matter (--- name / description ---)")
    meta = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        if value:
            meta[key.strip()] = value.strip()
    return meta, text[m.end():].strip()


@lru_cache(maxsize=1)
def _load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(HERE.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _parse(text)
        name = meta.get("name", "")
        if name != path.stem or not NAME.match(name) or not meta.get("description"):
            raise SkillError(f"{path.name}: name must match the file name and a description is needed")
        out[name] = {"name": name, "description": meta["description"], "body": body, "text": text}
    return out


def names() -> list[str]:
    return list(_load())


def catalog() -> list[dict]:
    """Name and description of every skill (what the robot sees before loading one)."""
    return [{"name": s["name"], "description": s["description"]} for s in _load().values()]


def get(name: str) -> dict:
    skill = _load().get(str(name or "").strip().lower())
    if skill is None:
        raise SkillError(f"unknown skill {name!r}: one of {', '.join(names())}")
    return skill


def catalog_text() -> str:
    return "\n".join(f"- {s['name']}: {s['description']}" for s in catalog())


def files(prefix: str = ".claude/skills") -> dict[str, str]:
    """``{path: text}`` of every skill as a Claude Code skill, for a commit or a folder."""
    return {f"{prefix}/{s['name']}/SKILL.md": s["text"] for s in _load().values()}


def export(dest: Path) -> list[Path]:
    """Write the skills under ``dest`` (a project folder): ``dest/.claude/skills/<name>/SKILL.md``."""
    written = []
    for rel, text in files().items():
        path = Path(dest) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
