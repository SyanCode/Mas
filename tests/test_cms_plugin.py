"""The Claude Code plugin (plugins/mas-cms) stays in sync with Mas: reference, manifests, check.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mas.cms import CmsError, parse_schema

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugins" / "mas-cms" / "skills" / "make-compatible"


def load_checker():
    spec = importlib.util.spec_from_file_location("mas_cms_check", SKILL / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load_checker()

FIELDS = [{"key": "title"}]
SCHEMAS = [
    {"pages": [{"id": "home", "file": "content/home.json", "fields": [
        {"key": "title", "type": "text", "max": 20, "required": True}, {"key": "cta", "type": "url"},
        {"key": "theme", "type": "select", "options": ["light", {"value": "dark", "label": "Dark"}]},
        {"key": "hero", "type": "image"}, {"key": "n", "type": "number", "min": 0, "integer": True},
        {"key": "items", "type": "list", "fields": [{"key": "name"}, {"key": "photo", "type": "image"}]}]}]},
    {"collections": [{"id": "articles", "dir": "content/articles", "fields": [
        {"key": "title"}, {"key": "date", "type": "date"}, {"key": "body", "type": "markdown"}]}]},
    {"collections": [{"id": "news", "storage": "supabase", "fields": FIELDS}]},
    {"$schema": check.SCHEMA_URL, "pages": [{"id": "a", "file": "a.json", "fields": FIELDS}]},
    # refused
    {"pages": []},
    {},
    {"pages": [{"id": "x", "file": "../a.json", "fields": FIELDS}]},
    {"pages": [{"id": "x", "file": ".github/a.json", "fields": FIELDS}]},
    {"pages": [{"id": "x", "file": "mas.cms.json", "fields": FIELDS}]},
    {"pages": [{"id": "x", "file": "a.yml", "fields": FIELDS}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": []}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a", "type": "html"}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a"}, {"key": "a"}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a", "type": "select"}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a", "type": "image", "url": "uploads"}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a", "type": "number", "min": "0"}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "a", "max": -1}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": [{"key": "l", "type": "list", "fields": [
        {"key": "n", "type": "list", "fields": FIELDS}]}]}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": FIELDS}, {"id": "x", "file": "b.json", "fields": FIELDS}]},
    {"pages": [{"id": "x", "file": "a.json", "fields": FIELDS}, {"id": "y", "file": "a.json", "fields": FIELDS}]},
    {"collections": [{"id": "a", "fields": FIELDS}]},
    {"collections": [{"id": "a", "dir": "posts", "fields": [{"key": "slug"}, {"key": "title"}]}]},
    {"collections": [{"id": "a", "dir": "posts", "fields": [{"key": "n", "type": "number"}]}]},
    {"collections": [{"id": "a", "dir": "posts", "fields": FIELDS, "sort_field": "nope"}]},
    {"collections": [{"id": "a", "dir": "posts", "fields": FIELDS, "index_fields": ["nope"]}]},
    {"collections": [{"id": "a", "storage": "mysql", "dir": "posts", "fields": FIELDS}]},
    {"collections": [{"id": "a", "storage": "supabase", "fields": [{"key": "Title"}]}]},
    {"collections": [{"id": "a", "storage": "supabase", "fields": FIELDS + [{"key": "updated_at"}]}]},
    {"collections": [{"id": "a", "storage": "supabase", "table": "My-Table", "fields": FIELDS}]},
    {"collections": [{"id": "a", "dir": "posts", "fields": FIELDS}, {"id": "b", "dir": "posts/x", "fields": FIELDS}]},
    {"pages": [{"id": "p", "file": "posts/p.json", "fields": FIELDS}],
     "collections": [{"id": "a", "dir": "posts", "fields": FIELDS}]},
    {"pages": [{"id": "a", "file": "a.json", "fields": FIELDS}],
     "collections": [{"id": "a", "dir": "posts", "fields": FIELDS}]},
]


@pytest.mark.parametrize("schema", SCHEMAS, ids=range(len(SCHEMAS)))
def test_check_py_agrees_with_mas(schema):
    try:
        parse_schema(json.dumps(schema))
        accepted = True
    except CmsError:
        accepted = False
    report = check.Report()
    check.parse_schema(schema, report)
    assert (not report.errors) == accepted, report.errors


def test_check_py_on_a_site(tmp_path):
    def write(rel, data):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    write("mas.cms.json", {"$schema": check.SCHEMA_URL, **SCHEMAS[0], **SCHEMAS[1]})
    write("content/home.json", {"title": "Hello", "cta": "/contact", "theme": "dark", "n": 2, "extra": {"a": 1},
                                "items": [{"name": "A"}, {"photo": "/uploads/a.png"}]})
    write("content/articles/index.json", [])
    write("content/articles/first-post.json", {"slug": "first-post", "title": "First", "date": "2026-01-02"})
    assert check.check(tmp_path).errors == []
    ok = subprocess.run([sys.executable, str(SKILL / "check.py"), str(tmp_path)], capture_output=True, text=True)
    assert ok.returncode == 0 and "Mas compatible" in ok.stdout

    write("content/home.json", {"title": "x" * 21, "cta": "javascript:alert(1)", "theme": "neon", "n": "2"})
    write("content/articles/Bad Name.json", {"title": "x"})
    write("content/articles/no-title.json", {"date": "02/01/2026"})
    (tmp_path / "content/articles/index.json").unlink()
    bad = subprocess.run([sys.executable, str(SKILL / "check.py"), str(tmp_path)], capture_output=True, text=True)
    assert bad.returncode == 1 and "error(s)" in bad.stdout
    errors = "\n".join(check.check(tmp_path).errors)
    for expected in ("21 characters", "javascript:alert(1)", "'neon'", "› n: must be a number", "index.json: missing",
                     "Bad Name.json: file names are slugs", "'02/01/2026' is not a date"):
        assert expected in errors, expected
    warnings = "\n".join(check.check(tmp_path).warnings)
    assert "no-title.json › title: required but empty" in warnings


def test_reference_and_manifests():
    assert (SKILL / "reference.md").read_bytes() == (ROOT / "docs" / "cms.md").read_bytes(), \
        "copy docs/cms.md to plugins/mas-cms/skills/make-compatible/reference.md"
    assert check.SCHEMA_URL in (ROOT / "mas" / "cms.py").read_text(encoding="utf-8")
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / "plugins" / "mas-cms" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == "mas-cms")
    assert (ROOT / entry["source"]).resolve() == (ROOT / "plugins" / "mas-cms").resolve()
    assert plugin["name"] == "mas-cms"
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    front = skill.split("---")[1]
    assert "name: make-compatible" in front and "description:" in front
    assert "/plugin install mas-cms@" + market["name"] in (ROOT / "docs" / "cms.md").read_text(encoding="utf-8")
