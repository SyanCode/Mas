#!/usr/bin/env python3
"""Check that a site is Mas compatible: mas.cms.json and the files it points to.

Run from the root of the site's repository: ``python3 check.py [path/to/repo]``.
Standard library only. Exit status 1 when Mas would refuse something.
The rules follow mas/cms.py in https://github.com/SyanCode/Mas (tests keep them in sync).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_FILE = "mas.cms.json"
SCHEMA_URL = "https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json"
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
URL_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")
LINK_RE = re.compile(r"^(https?://[^\s<>\"']+|mailto:[^\s<>\"']+|tel:[+0-9 ().-]+|/[^\s<>\"']*|#[^\s<>\"']*)$")
EMAIL_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SCALAR_TYPES = {"text", "textarea", "markdown", "number", "boolean", "select", "url", "email", "date", "color",
                "image"}
DEFAULT_MAX = {"text": 200, "textarea": 2000, "markdown": 20000}
RESERVED = {"slug", "created_at", "updated_at"}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def unique(keys: list, where: str, r: Report) -> None:
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        r.error(f"{where}: duplicate {', '.join(map(str, dupes))}")


def repo_path(value: Any, where: str, r: Report) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\\" in value or "\x00" in value:
        r.error(f"{where}: invalid path {value!r}")
        return None
    p = PurePosixPath(value.strip())
    if p.is_absolute() or any(part in ("..", ".", "") for part in p.parts):
        r.error(f"{where}: {value!r} must be a relative path inside the repository")
        return None
    if p.parts[0] in (".git", ".github") or str(p) == SCHEMA_FILE:
        r.error(f"{where}: {p} cannot be edited")
        return None
    return str(p)


# -- mas.cms.json ------------------------------------------------------------------


def parse_field(raw: Any, where: str, nested: bool, r: Report) -> dict | None:
    if not isinstance(raw, dict):
        r.error(f"{where}: a field must be an object")
        return None
    key, ftype = raw.get("key"), raw.get("type", "text")
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        r.error(f"{where}: invalid key {key!r} (letters, digits, _ and -)")
        return None
    where = f"{where}.{key}"
    if ftype not in SCALAR_TYPES and not (ftype == "list" and not nested):
        r.error(f"{where}: unknown type {ftype!r}" + (" (lists cannot be nested)" if ftype == "list" else ""))
        return None
    field = dict(raw, type=ftype, required=bool(raw.get("required", False)))
    if ftype in DEFAULT_MAX:
        field["max"], field["min"] = raw.get("max", DEFAULT_MAX[ftype]), raw.get("min", 0)
        if not (isinstance(field["min"], int) and isinstance(field["max"], int)
                and 0 <= field["min"] <= field["max"] <= 100_000):
            r.error(f"{where}: invalid min/max")
    elif ftype == "number":
        for bound in ("min", "max"):
            value = raw.get(bound)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                r.error(f"{where}: {bound} must be a number")
    elif ftype == "select":
        values = []
        for opt in raw.get("options") or []:
            value = opt.get("value") if isinstance(opt, dict) else opt
            if not isinstance(value, str):
                r.error(f"{where}: options must be strings or {{value, label}}")
            values.append(value)
        source = raw.get("options_from")
        if source is not None:  # choices kept in a list of a page: any value of that list is accepted
            keys = ("page", "list", "value")
            if not isinstance(source, dict) or not all(isinstance(source.get(k), str) for k in keys):
                r.error(f'{where}.options_from: {{"page": id, "list": key, "value": key, "label": key}}')
            field["options_from"] = source
        elif not values:
            r.error(f"{where}: a select needs options (or options_from)")
        field["values"] = values
    elif ftype == "image":
        repo_path(raw.get("dir", "public/uploads"), f"{where}.dir", r)
        url = raw.get("url", "/uploads")
        if not isinstance(url, str) or not URL_PREFIX_RE.fullmatch(url):
            r.error(f"{where}.url: must be a path such as /uploads")
    elif ftype == "list":
        subs = raw.get("fields")
        if not isinstance(subs, list) or not subs:
            r.error(f"{where}: a list needs fields")
            subs = []
        field["fields"] = [f for f in (parse_field(s, where, True, r) for s in subs) if f]
        unique([f["key"] for f in field["fields"]], where, r)
    return field


def parse_fields(raw: Any, where: str, r: Report) -> list[dict]:
    if not isinstance(raw, list) or not raw or len(raw) > 100:
        r.error(f"{where}: needs between 1 and 100 fields")
        return []
    fields = [f for f in (parse_field(f, where, False, r) for f in raw) if f]
    unique([f["key"] for f in fields], where, r)
    return fields


def parse_collection(c: dict, cid: str, r: Report) -> dict:
    storage = c.get("storage", "git")
    out: dict = {"id": cid, "storage": storage, "dir": None, "table": None}
    if storage not in ("git", "supabase"):
        r.error(f'{cid}.storage: "git" (files in the repository) or "supabase" (a table)')
    if storage == "git":
        out["dir"] = repo_path(c.get("dir"), f"{cid}.dir", r)
    elif storage == "supabase":
        table = c.get("table") or cid.replace("-", "_")
        if not isinstance(table, str) or not COLUMN_RE.fullmatch(table):
            r.error(f"{cid}.table: lowercase letters, digits and _ (articles)")
        out["table"] = table
    fields = out["fields"] = parse_fields(c.get("fields"), cid, r)
    if any(f["key"] == "slug" for f in fields):
        r.error(f"{cid}: slug is set by Mas, it cannot be a field")
    for f in fields if storage == "supabase" else []:
        if not COLUMN_RE.fullmatch(f["key"]) or f["key"] in RESERVED:
            r.error(f"{cid}.{f['key']}: in a Supabase table, keys are lowercase letters, digits and _ "
                    "(not created_at or updated_at)")
    by_key = {f["key"]: f for f in fields}
    title = c.get("title_field") or next((f["key"] for f in fields if f["type"] == "text"), None)
    if fields and by_key.get(title, {}).get("type") != "text":
        r.error(f"{cid}.title_field: needs a text field (the name of each entry)")
    out["title_field"] = title
    sort = c.get("sort_field") or next((f["key"] for f in fields if f["type"] == "date"), None)
    if sort is not None and fields and by_key.get(sort, {}).get("type") not in ("date", "number", "text"):
        r.error(f"{cid}.sort_field: needs a date, number or text field")
    summary = c.get("index_fields")
    if summary is not None and (not isinstance(summary, list) or any(k not in by_key for k in summary)):
        r.error(f"{cid}.index_fields: list of field keys")
    return out


def parse_schema(data: Any, r: Report) -> dict:
    empty: dict = {"pages": [], "collections": []}
    if not isinstance(data, dict):
        r.error(f"{SCHEMA_FILE} must be a JSON object")
        return empty
    pages_raw, collections_raw = data.get("pages", []), data.get("collections", [])
    if not isinstance(pages_raw, list) or not isinstance(collections_raw, list):
        r.error(f'{SCHEMA_FILE}: "pages" and "collections" are lists')
        return empty
    if not (pages_raw or collections_raw):
        r.error(f'{SCHEMA_FILE} needs a non-empty "pages" or "collections" list')
        return empty
    if len(pages_raw) > 50:
        r.error("at most 50 pages")
    if len(collections_raw) > 20:
        r.error("at most 20 collections")
    pages, collections = [], []
    for i, p in enumerate(pages_raw):
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or not KEY_RE.fullmatch(pid):
            r.error(f"pages[{i}]: invalid id {pid!r}")
            continue
        file = repo_path(p.get("file"), f"{pid}.file", r)
        if file and not file.endswith(".json"):
            r.error(f"{pid}.file: content files must be .json")
        pages.append({"id": pid, "file": file, "fields": parse_fields(p.get("fields"), pid, r)})
    for i, c in enumerate(collections_raw):
        cid = c.get("id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not KEY_RE.fullmatch(cid):
            r.error(f"collections[{i}]: invalid id {cid!r}")
            continue
        collections.append(parse_collection(c, cid, r))
    unique([p["id"] for p in pages] + [c["id"] for c in collections], "pages and collections", r)
    unique([p["file"] for p in pages if p["file"]], "page files", r)
    unique([c["table"] for c in collections if c["table"]], "collection tables", r)
    dirs = [c["dir"] for c in collections if c["dir"]]
    files = [p["file"] for p in pages if p["file"]]
    for c in (c for c in collections if c["dir"]):
        inside = [d for d in dirs if d != c["dir"] and (d + "/").startswith(c["dir"] + "/")]
        inside += [f for f in files if PurePosixPath(f).parent == PurePosixPath(c["dir"])]
        if inside or dirs.count(c["dir"]) > 1:
            r.error(f"{c['id']}.dir: {c['dir']} must only hold the entries of this collection")
    return {"pages": pages, "collections": collections}


# -- the content files -------------------------------------------------------------


def check_value(field: dict, value: Any, where: str, r: Report) -> None:
    """A value already in the files: the client could not save the form if it breaks a rule."""
    t = field["type"]
    if value is None or (value == "" and t != "boolean"):
        if field["required"]:
            r.warn(f"{where}: required but empty")
        return
    if t in DEFAULT_MAX:
        if not isinstance(value, str):
            r.error(f"{where}: must be text")
        elif CONTROL_RE.search(value) or (t == "text" and "\n" in value):
            r.error(f"{where}: forbidden characters (a text field is one line)")
        elif isinstance(field.get("max"), int) and len(value) > field["max"]:
            r.error(f"{where}: {len(value)} characters, max is {field['max']}")
    elif t == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        r.error(f"{where}: must be a number")
    elif t == "boolean" and not isinstance(value, bool):
        r.error(f"{where}: must be true or false")
    elif t == "select" and not field.get("options_from") and value not in field.get("values", []):
        r.error(f"{where}: {value!r} is not one of the options")
    elif t == "url" and not (isinstance(value, str) and LINK_RE.fullmatch(value)):
        r.error(f"{where}: {value!r} is not an accepted link (https://, /path, #anchor, mailto:, tel:)")
    elif t == "email" and not (isinstance(value, str) and EMAIL_RE.fullmatch(value)):
        r.error(f"{where}: {value!r} is not an email address")
    elif t == "date" and not (isinstance(value, str) and DATE_RE.fullmatch(value)):
        r.error(f"{where}: {value!r} is not a date (YYYY-MM-DD)")
    elif t == "color" and not (isinstance(value, str) and COLOR_RE.fullmatch(value)):
        r.error(f"{where}: {value!r} is not a color (#rrggbb)")
    elif t == "list":
        if not isinstance(value, list) or not all(isinstance(i, dict) for i in value):
            r.error(f"{where}: must be a list of objects")
            return
        for n, item in enumerate(value, 1):
            for sub in field["fields"]:
                if sub["key"] in item or sub["required"]:
                    check_value(sub, item.get(sub["key"]), f"{where} #{n}.{sub['key']}", r)


def load_json(path: Path, shown: str, r: Report) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        r.error(f"{shown}: not valid JSON ({exc})")
        return None


def check_files(root: Path, schema: dict, r: Report) -> None:
    for page in schema["pages"]:
        if not page["file"]:
            continue
        path = root / page["file"]
        if not path.is_file():
            r.warn(f"{page['file']}: missing (Mas creates it on the first save, but the site must build without it)")
            continue
        data = load_json(path, page["file"], r)
        if data is not None and not isinstance(data, dict):
            r.error(f"{page['file']}: must contain a JSON object")
        elif isinstance(data, dict):
            for f in page["fields"]:
                check_value(f, data.get(f["key"]), f"{page['file']} › {f['key']}", r)
    for c in schema["collections"]:
        if c["storage"] != "git" or not c["dir"]:
            continue
        folder = root / c["dir"]
        if not (folder / "index.json").is_file():
            r.error(f"{c['dir']}/index.json: missing, commit it (holding [] when there is no entry yet)")
        elif not isinstance(load_json(folder / "index.json", f"{c['dir']}/index.json", r), list):
            r.error(f"{c['dir']}/index.json: must be a list")
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            shown = f"{c['dir']}/{path.name}"
            if path.name == "index.json":
                continue
            if not SLUG_RE.fullmatch(path.stem):
                r.error(f"{shown}: file names are slugs (a-z, 0-9, dashes): Mas ignores this one")
                continue
            data = load_json(path, shown, r)
            if not isinstance(data, dict):
                r.error(f"{shown}: must contain a JSON object")
                continue
            if data.get("slug") not in (None, path.stem):
                r.warn(f"{shown}: slug {data.get('slug')!r} differs from the file name")
            for f in c["fields"]:
                rule = dict(f, required=True) if f["key"] == c["title_field"] else f
                check_value(rule, data.get(f["key"]), f"{shown} › {f['key']}", r)


def check(root: Path) -> Report:
    r = Report()
    path = root / SCHEMA_FILE
    if not path.is_file():
        r.error(f"no {SCHEMA_FILE} at {root.resolve().as_posix()}")
        return r
    data = load_json(path, SCHEMA_FILE, r)
    if data is None:
        return r
    if isinstance(data, dict) and "$schema" not in data:
        r.warn(f'add "$schema": "{SCHEMA_URL}" for completion in editors')
    schema = parse_schema(data, r)
    if not r.errors:
        check_files(root, schema, r)
    return r


def main() -> int:
    r = check(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    for w in r.warnings:
        print(f"warning: {w}")
    for e in r.errors:
        print(f"error: {e}")
    if r.errors:
        print(f"\n{len(r.errors)} error(s): Mas would refuse this {SCHEMA_FILE}, or the client could not save.")
        return 1
    print(f"Mas compatible: {SCHEMA_FILE} and its files follow the rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
