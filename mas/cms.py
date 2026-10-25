"""Git-based CMS: the repository declares what may be edited, Mas edits only that.

A project opts in with ``mas.cms.json`` at the root of its repository::

    {"pages": [{"id": "home", "label": "Home page", "file": "content/home.json",
                "fields": [{"key": "title", "type": "text", "max": 80, "required": true},
                           {"key": "hero", "type": "image", "dir": "public/uploads", "url": "/uploads"},
                           {"key": "services", "type": "list", "max_items": 6,
                            "fields": [{"key": "name", "type": "text"}]}]}]}

Each page is one JSON file the site reads at build time. Editors fill a form
generated from the schema; values are validated against it, keys the schema
does not declare are kept untouched, and the change becomes one commit on the
deployed branch (which redeploys the app). The schema is always read from the
repository, never from the browser.

Collections (articles, news, projects…) are folders of entries the editor adds
and deletes::

    {"collections": [{"id": "articles", "label": "Articles", "dir": "content/articles",
                      "fields": [{"key": "title", "type": "text", "required": true},
                                 {"key": "date", "type": "date"},
                                 {"key": "body", "type": "markdown"}]}]}

Each entry is ``<dir>/<slug>.json`` (with its ``slug``), and Mas rewrites
``<dir>/index.json`` in the same commit: every entry without its markdown
fields, newest first. The site decides how to show them (the latest three on
the home page, a page with all of them, one page per slug).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

from mas.db import Database
from mas.envcrypt import EnvError, decrypt_env, load_fernet
from mas.github import GitHubApp, GitHubError
from mas.supabase import SupabaseError

log = logging.getLogger(__name__)

SCHEMA_FILE = "mas.cms.json"
SCHEMA_URL = "https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json"  # editor completion
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")
URL_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")
LINK_RE = re.compile(r"^(https?://[^\s<>\"']+|mailto:[^\s<>\"']+|tel:[+0-9 ().-]+|/[^\s<>\"']*|#[^\s<>\"']*)$")
EMAIL_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

SCALAR_TYPES = {"text", "textarea", "markdown", "number", "boolean", "select", "url", "email", "date", "color",
                "image"}
DEFAULT_MAX = {"text": 200, "textarea": 2000, "markdown": 20000}
MAX_PAGES, MAX_FIELDS, MAX_ITEMS = 50, 100, 200
MAX_COLLECTIONS, MAX_ENTRIES = 20, 1000
INDEX_FILE = "index.json"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 80
MAX_UPLOAD_KB = 8192
UPLOAD_TTL = 24 * 3600
COMPAT_TTL = 600  # compatibility of a repository, cached 10 minutes
FORBIDDEN_DIRS = (".git", ".github")
IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              "avif": "image/avif"}


class CmsError(Exception):
    pass


class CmsConflict(CmsError):
    """The content changed on GitHub since the editor opened it."""


from mas import cms_supabase  # noqa: E402  (it uses the errors above)


def detect_image(data: bytes) -> str | None:
    """Extension from the magic bytes (SVG is refused: it can carry scripts)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    return None


def safe_repo_path(path: Any, what: str) -> str:
    if not isinstance(path, str) or not path.strip() or "\\" in path or "\x00" in path:
        raise CmsError(f"{what}: invalid path")
    p = PurePosixPath(path.strip())
    if p.is_absolute() or any(part in ("..", ".", "") for part in p.parts):
        raise CmsError(f"{what}: must be a relative path inside the repository")
    if p.parts[0] in FORBIDDEN_DIRS or str(p) == SCHEMA_FILE:
        raise CmsError(f"{what}: {p} cannot be edited")
    return str(p)


# -- schema --------------------------------------------------------------------


def _parse_field(raw: Any, where: str, nested: bool) -> dict:
    if not isinstance(raw, dict):
        raise CmsError(f"{where}: a field must be an object")
    key, ftype = raw.get("key"), raw.get("type", "text")
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise CmsError(f"{where}: invalid key {key!r} (letters, digits, _ and -)")
    where = f"{where}.{key}"
    if ftype not in SCALAR_TYPES and not (ftype == "list" and not nested):
        raise CmsError(f"{where}: unknown type {ftype!r}" + (" (lists cannot be nested)" if ftype == "list" else ""))
    field: dict = {"key": key, "type": ftype, "label": str(raw.get("label") or key)[:100],
                   "help": str(raw.get("help") or "")[:300], "required": bool(raw.get("required", False))}
    if ftype in DEFAULT_MAX:
        field["max"] = int(raw.get("max", DEFAULT_MAX[ftype]))
        field["min"] = int(raw.get("min", 0))
        if not 0 <= field["min"] <= field["max"] <= 100_000:
            raise CmsError(f"{where}: invalid min/max")
    elif ftype == "number":
        field["integer"] = bool(raw.get("integer", False))
        for bound in ("min", "max"):
            if raw.get(bound) is not None:
                if isinstance(raw[bound], bool) or not isinstance(raw[bound], (int, float)):
                    raise CmsError(f"{where}: {bound} must be a number")
                field[bound] = raw[bound]
    elif ftype == "select":
        options = []
        for opt in raw.get("options") or []:
            value, label = (opt.get("value"), opt.get("label")) if isinstance(opt, dict) else (opt, opt)
            if not isinstance(value, str):
                raise CmsError(f"{where}: options must be strings or {{value, label}}")
            options.append({"value": value, "label": str(label if label is not None else value)})
        source = raw.get("options_from")
        if source is not None:
            # Choices kept in a list of a page (a "Categories" page the client edits); checked by parse_schema.
            if not isinstance(source, dict) or not all(isinstance(source.get(k), str) and KEY_RE.fullmatch(source[k])
                                                       for k in ("page", "list", "value")):
                raise CmsError(f'{where}.options_from: {{"page": id, "list": key, "value": key, "label": key}}')
            label_key = source.get("label", source["value"])
            if not isinstance(label_key, str) or not KEY_RE.fullmatch(label_key):
                raise CmsError(f"{where}.options_from.label: invalid key")
            field["options_from"] = {"page": source["page"], "list": source["list"], "value": source["value"],
                                     "label": label_key}
        elif not options:
            raise CmsError(f"{where}: a select needs options (or options_from)")
        field["options"] = options  # with options_from: used until the page lists something
    elif ftype == "image":
        field["dir"] = safe_repo_path(raw.get("dir", "public/uploads"), f"{where}.dir")
        url = raw.get("url", "/uploads")
        if not isinstance(url, str) or not URL_PREFIX_RE.fullmatch(url):
            raise CmsError(f"{where}.url: must be a path such as /uploads")
        field["url"] = url.rstrip("/") or "/"
        field["max_kb"] = min(int(raw.get("max_kb", 2048)), MAX_UPLOAD_KB)
    elif ftype == "list":
        subs = raw.get("fields")
        if not isinstance(subs, list) or not subs:
            raise CmsError(f"{where}: a list needs fields")
        field["fields"] = [_parse_field(f, where, nested=True) for f in subs[:MAX_FIELDS]]
        _unique([f["key"] for f in field["fields"]], where)
        field["min_items"] = int(raw.get("min_items", 0))
        field["max_items"] = min(int(raw.get("max_items", 50)), MAX_ITEMS)
        field["item_label"] = str(raw.get("item_label") or "Item")[:60]
    if raw.get("placeholder"):
        field["placeholder"] = str(raw["placeholder"])[:200]
    return field


def _unique(keys: list[str], where: str) -> None:
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise CmsError(f"{where}: duplicate {', '.join(sorted(dupes))}")


def _parse_fields(raw: Any, where: str) -> list[dict]:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_FIELDS:
        raise CmsError(f"{where}: needs between 1 and {MAX_FIELDS} fields")
    fields = [_parse_field(f, where, nested=False) for f in raw]
    _unique([f["key"] for f in fields], where)
    return fields


def _parse_collection(c: dict, cid: str) -> dict:
    storage = c.get("storage", "git")
    if storage not in ("git", "supabase"):
        raise CmsError(f'{cid}.storage: "git" (files in the repository) or "supabase" (a table)')
    folder, table = None, None
    if storage == "git":
        folder = safe_repo_path(c.get("dir"), f"{cid}.dir")
    else:
        table = c.get("table") or cid.replace("-", "_")
        if not isinstance(table, str) or not cms_supabase.COLUMN_RE.fullmatch(table):
            raise CmsError(f"{cid}.table: lowercase letters, digits and _ (articles)")
    fields = _parse_fields(c.get("fields"), cid)
    if any(f["key"] == "slug" for f in fields):
        raise CmsError(f"{cid}: slug is set by Mas, it cannot be a field")
    for f in fields if storage == "supabase" else []:
        if not cms_supabase.COLUMN_RE.fullmatch(f["key"]) or f["key"] in cms_supabase.RESERVED:
            raise CmsError(f"{cid}.{f['key']}: in a Supabase table, keys are lowercase letters, digits and _ "
                           "(not created_at or updated_at)")
    by_key = {f["key"]: f for f in fields}
    title = c.get("title_field") or next((f["key"] for f in fields if f["type"] == "text"), None)
    if by_key.get(title, {}).get("type") != "text":
        raise CmsError(f"{cid}.title_field: needs a text field (the name of each entry)")
    by_key[title]["required"] = True
    sort = c.get("sort_field") or next((f["key"] for f in fields if f["type"] == "date"), None)
    if sort is not None and by_key.get(sort, {}).get("type") not in ("date", "number", "text"):
        raise CmsError(f"{cid}.sort_field: needs a date, number or text field")
    summary = c.get("index_fields")
    if summary is None:
        summary = [f["key"] for f in fields if f["type"] != "markdown"]
    elif not isinstance(summary, list) or any(k not in by_key for k in summary):
        raise CmsError(f"{cid}.index_fields: list of field keys")
    return {"id": cid, "label": str(c.get("label") or cid)[:100], "description": str(c.get("description") or "")[:500],
            "item_label": str(c.get("item_label") or "Entry")[:60], "storage": storage, "dir": folder,
            "table": table, "fields": fields,
            "title_field": title, "sort_field": sort, "index_fields": list(dict.fromkeys(summary)),
            "max_entries": max(1, min(int(c.get("max_entries", MAX_ENTRIES)), MAX_ENTRIES))}


def parse_schema(raw: bytes | str) -> dict:
    """Validate ``mas.cms.json``; returns a normalized copy or raises CmsError."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CmsError(f"{SCHEMA_FILE} is not valid JSON: {exc}") from None
    pages_raw = data.get("pages", []) if isinstance(data, dict) else None
    collections_raw = data.get("collections", []) if isinstance(data, dict) else None
    if not isinstance(pages_raw, list) or not isinstance(collections_raw, list) or not (pages_raw or collections_raw):
        raise CmsError(f'{SCHEMA_FILE} needs a non-empty "pages" or "collections" list')
    if len(pages_raw) > MAX_PAGES:
        raise CmsError(f"at most {MAX_PAGES} pages")
    if len(collections_raw) > MAX_COLLECTIONS:
        raise CmsError(f"at most {MAX_COLLECTIONS} collections")
    pages = []
    for i, p in enumerate(pages_raw):
        if not isinstance(p, dict):
            raise CmsError(f"pages[{i}] must be an object")
        pid = p.get("id")
        if not isinstance(pid, str) or not KEY_RE.fullmatch(pid):
            raise CmsError(f"pages[{i}]: invalid id {pid!r}")
        file = safe_repo_path(p.get("file"), f"{pid}.file")
        if not file.endswith(".json"):
            raise CmsError(f"{pid}.file: content files must be .json")
        pages.append({"id": pid, "label": str(p.get("label") or pid)[:100],
                      "description": str(p.get("description") or "")[:500], "file": file,
                      "fields": _parse_fields(p.get("fields"), pid)})
    collections = []
    for i, c in enumerate(collections_raw):
        if not isinstance(c, dict):
            raise CmsError(f"collections[{i}] must be an object")
        cid = c.get("id")
        if not isinstance(cid, str) or not KEY_RE.fullmatch(cid):
            raise CmsError(f"collections[{i}]: invalid id {cid!r}")
        collections.append(_parse_collection(c, cid))
    _unique([p["id"] for p in pages] + [c["id"] for c in collections], "pages and collections")
    for field, where in _selects_from(pages + collections):
        src = field["options_from"]
        page = next((p for p in pages if p["id"] == src["page"]), None)
        lst = next((f for f in (page or {}).get("fields", []) if f["key"] == src["list"]), None)
        if lst is None or lst["type"] != "list":
            raise CmsError(f"{where}.options_from: no list {src['list']!r} in a page {src['page']!r}")
        keys = {f["key"] for f in lst["fields"]}
        if src["value"] not in keys or src["label"] not in keys:
            raise CmsError(f"{where}.options_from: the items of {src['page']}.{src['list']} have no "
                           f"{src['value']!r} / {src['label']!r} field")
    _unique([p["file"] for p in pages], "page files")
    _unique([c["table"] for c in collections if c["table"]], "collection tables")
    dirs = [c["dir"] for c in collections if c["dir"]]
    for c in (c for c in collections if c["dir"]):  # a folder belongs to one collection, and holds no page
        inside = [d for d in dirs if d != c["dir"] and (d + "/").startswith(c["dir"] + "/")]
        inside += [p["file"] for p in pages if PurePosixPath(p["file"]).parent == PurePosixPath(c["dir"])]
        if inside or dirs.count(c["dir"]) > 1:
            raise CmsError(f"{c['id']}.dir: {c['dir']} must only hold the entries of this collection")
    return {"pages": pages, "collections": collections}


def _selects_from(parts: list[dict]) -> list[tuple[dict, str]]:
    """Select fields whose choices come from a page (``options_from``), with where they are."""
    out = []
    for part in parts:
        for f in part["fields"]:
            for sub in [f, *f.get("fields", [])]:
                if sub.get("options_from"):
                    out.append((sub, f"{part['id']}.{sub['key']}"))
    return out


def resolve_options(schema: dict, read_page: Callable[[dict], dict | None]) -> dict:
    """Fill the choices of ``options_from`` selects from the current content of their page."""
    cache: dict[str, dict | None] = {}
    for field, _ in _selects_from(schema["pages"] + schema["collections"]):
        src = field["options_from"]
        page = next(p for p in schema["pages"] if p["id"] == src["page"])
        if page["id"] not in cache:
            try:
                cache[page["id"]] = read_page(page)
            except CmsError:
                cache[page["id"]] = None
        items = (cache[page["id"]] or {}).get(src["list"])
        options, seen = [], set()
        for item in items if isinstance(items, list) else []:
            value = item.get(src["value"]) if isinstance(item, dict) else None
            if isinstance(value, str) and value.strip() and value.strip() not in seen:
                seen.add(value.strip())
                label = item.get(src["label"])
                options.append({"value": value.strip(), "label": str(label).strip() if label else value.strip()})
        if options:
            field["options"] = options
    return schema


def image_fields(schema: dict) -> list[dict]:
    out = []
    git = [c for c in schema.get("collections", []) if c["storage"] == "git"]  # Supabase images are in Storage
    for page in schema["pages"] + git:
        for f in page["fields"]:
            out += [f] if f["type"] == "image" else [s for s in f.get("fields", []) if s["type"] == "image"]
    return out


# -- values --------------------------------------------------------------------


def _strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_strings(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_strings(v) for v in value)) if value else set()
    return set()


def _empty(field: dict) -> Any:
    return {"boolean": False, "list": [], "number": None}.get(field["type"], "")


class _Validator:
    def __init__(self, existing: dict, resolve_upload: Callable[[str], tuple[bytes, str, str]]) -> None:
        self.known = _strings(existing)  # image paths already in the file stay accepted
        self.resolve_upload = resolve_upload
        self.files: dict[str, bytes] = {}
        self.uploads: list[str] = []

    def check(self, field: dict, value: Any, label: str) -> Any:
        t = field["type"]
        if value is None or (value == "" and t != "boolean"):
            if field["required"]:
                raise CmsError(f"{label} is required")
            return _empty(field)
        if t in DEFAULT_MAX:
            if not isinstance(value, str):
                raise CmsError(f"{label} must be text")
            value = value.replace("\r\n", "\n")
            if CONTROL_RE.search(value) or (t == "text" and "\n" in value):
                raise CmsError(f"{label} contains forbidden characters")
            if not field["min"] <= len(value) <= field["max"]:
                raise CmsError(f"{label}: between {field['min']} and {field['max']} characters")
            return value
        if t == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CmsError(f"{label} must be a number")
            if field["integer"] and value != int(value):
                raise CmsError(f"{label} must be a whole number")
            if ("min" in field and value < field["min"]) or ("max" in field and value > field["max"]):
                raise CmsError(f"{label} is out of range")
            return int(value) if field["integer"] else value
        if t == "boolean":
            if not isinstance(value, bool):
                raise CmsError(f"{label} must be true or false")
            return value
        if t == "select":
            if value not in {o["value"] for o in field["options"]}:
                raise CmsError(f"{label}: not one of the allowed choices")
            return value
        if not isinstance(value, str) or len(value) > 2000:
            raise CmsError(f"{label} is invalid")
        if t == "url" and not LINK_RE.fullmatch(value):
            raise CmsError(f"{label}: use a link starting with https://, /, mailto: or tel:")
        if t == "email" and not EMAIL_RE.fullmatch(value):
            raise CmsError(f"{label} is not a valid email address")
        if t == "date" and not DATE_RE.fullmatch(value):
            raise CmsError(f"{label} must be a date (YYYY-MM-DD)")
        if t == "color" and not COLOR_RE.fullmatch(value):
            raise CmsError(f"{label} must be a color like #1a2b3c")
        if t == "image":
            return self.image(field, value, label)
        return value

    def image(self, field: dict, value: str, label: str) -> str:
        if value.startswith("mas-upload:"):
            token = value[len("mas-upload:"):]
            data, ext, stem = self.resolve_upload(token)
            if len(data) > field["max_kb"] * 1024:
                raise CmsError(f"{label}: image larger than {field['max_kb']} KB")
            name = f"{stem}-{secrets.token_hex(3)}.{ext}"
            self.files[f"{field['dir']}/{name}"] = data
            self.uploads.append(token)
            return f"{field['url'].rstrip('/')}/{name}"
        prefix = field["url"].rstrip("/") + "/"
        if value in self.known or (value.startswith(prefix) and FILENAME_RE.fullmatch(value[len(prefix):])):
            return value
        raise CmsError(f"{label}: upload an image instead of typing a path")

    def page(self, page: dict, incoming: dict, existing: dict) -> dict:
        merged = dict(existing)  # undeclared keys are kept as they are
        for field in page["fields"]:
            key, label = field["key"], field["label"]
            if key not in incoming:
                continue
            value = incoming[key]
            if field["type"] != "list":
                merged[key] = self.check(field, value, label)
                continue
            if value is None:
                value = []
            if not isinstance(value, list):
                raise CmsError(f"{label} must be a list")
            if not field["min_items"] <= len(value) <= field["max_items"]:
                raise CmsError(f"{label}: between {field['min_items']} and {field['max_items']} items")
            declared = {f["key"] for f in field["fields"]}
            items = []
            for i, item in enumerate(value, 1):
                if not isinstance(item, dict):
                    raise CmsError(f"{label} #{i} is invalid")
                clean = {k: v for k, v in item.items() if k not in declared}
                for sub in field["fields"]:
                    if sub["key"] not in item and not sub["required"]:
                        continue  # absent stays absent: the site may test for the key
                    clean[sub["key"]] = self.check(sub, item.get(sub["key"]), f"{label} #{i} › {sub['label']}")
                items.append(clean)
            merged[key] = items
        return merged


def apply_values(page: dict, incoming: dict, existing: dict,
                 resolve_upload: Callable[[str], tuple[bytes, str, str]]) -> tuple[dict, dict[str, bytes], list[str]]:
    """Validated page content, new image files, upload tokens consumed."""
    if not isinstance(incoming, dict):
        raise CmsError("values must be an object")
    v = _Validator(existing, resolve_upload)
    return v.page(page, incoming, existing), v.files, v.uploads


def dump_json(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


# -- collections -----------------------------------------------------------------


def slugify(text: str) -> str:
    """URL name of an entry: "Été à Paris !" → "ete-a-paris"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:SLUG_MAX].strip("-") or "entry"


def index_entry(collection: dict, slug: str, data: dict) -> dict:
    return {"slug": slug, **{k: data[k] for k in collection["index_fields"] if k in data}}


def sort_index(collection: dict, entries: list[dict]) -> list[dict]:
    """Newest first on the sort field (entries without a value last), then by slug."""
    entries = sorted(entries, key=lambda e: e["slug"])
    key = collection["sort_field"]
    if key is None:
        return entries

    def has(e: dict) -> bool:
        v = e.get(key)
        return isinstance(v, (str, int, float)) and not isinstance(v, bool) and v != ""
    dated = sorted((e for e in entries if has(e)), key=lambda e: (isinstance(e[key], str), e[key]), reverse=True)
    return dated + [e for e in entries if not has(e)]


# -- schema generation -----------------------------------------------------------

NOT_CONTENT = {"package.json", "package-lock.json", "composer.json", "jsconfig.json", "components.json",
               "vercel.json", "netlify.json", "renovate.json", "turbo.json", "lerna.json", "nx.json",
               "manifest.json", "site.webmanifest", "deno.json", "bun.lock", SCHEMA_FILE}
NOT_CONTENT_DIRS = {"node_modules", ".git", ".github", ".vscode", ".idea", ".next", "dist", "build", "out",
                    "vendor", "coverage", ".turbo", ".vercel", "__tests__", "test", "tests"}
CONTENT_HINTS = ("content", "data", "locales", "i18n", "cms", "copy", "texts")
IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|webp|gif|avif)$", re.I)
MARKDOWN_RE = re.compile(r"(\*\*[^*]+\*\*|^#{1,6} |\[[^\]]+\]\([^)]+\)|^[-*] )", re.M)
MAX_CANDIDATE_BYTES = 200_000


def content_candidates(files: list[dict]) -> list[dict]:
    """JSON files that may hold site content, most likely first."""
    out = []
    for f in files:
        p = PurePosixPath(f["path"])
        name = p.name.lower()
        if p.suffix.lower() != ".json" or name in NOT_CONTENT or name.startswith(("tsconfig", ".eslintrc", ".")):
            continue
        if set(part.lower() for part in p.parts[:-1]) & NOT_CONTENT_DIRS or f.get("size", 0) > MAX_CANDIDATE_BYTES:
            continue
        hinted = any(h in part.lower() for part in p.parts[:-1] for h in CONTENT_HINTS)
        out.append({"path": f["path"], "size": f.get("size", 0), "suggested": hinted})
    out.sort(key=lambda c: (not c["suggested"], c["path"]))
    return out[:100]


def humanize(key: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ").replace("-", " ").split()
    return " ".join(words).capitalize() or key


def _cap(length: int, floor: int, ceiling: int) -> int:
    return max(floor, min(ceiling, length * 2))


def _infer_scalar(key: str, value: Any, image_dirs: Callable[[str], str]) -> dict | None:
    base = {"key": key, "label": humanize(key)}
    if isinstance(value, bool):
        return {**base, "type": "boolean"}
    if isinstance(value, int):
        return {**base, "type": "number", "integer": True}
    if isinstance(value, float):
        return {**base, "type": "number"}
    if value is None:
        return {**base, "type": "text", "max": 200}
    if not isinstance(value, str):
        return None
    if value.startswith("/") and IMAGE_EXT_RE.search(value) and " " not in value:
        url = str(PurePosixPath(value).parent).rstrip("/") or "/"
        return {**base, "type": "image", "url": url, "dir": image_dirs(url)}
    if COLOR_RE.fullmatch(value):
        return {**base, "type": "color"}
    if DATE_RE.fullmatch(value):
        return {**base, "type": "date"}
    if EMAIL_RE.fullmatch(value):
        return {**base, "type": "email"}
    if re.match(r"^(https?://|mailto:|tel:)", value) and LINK_RE.fullmatch(value):
        return {**base, "type": "url"}
    if MARKDOWN_RE.search(value):
        return {**base, "type": "markdown", "max": _cap(len(value), 2000, 20000)}
    if "\n" in value or len(value) > 120:
        return {**base, "type": "textarea", "max": _cap(len(value), 500, 5000)}
    return {**base, "type": "text", "max": _cap(len(value), 80, 200)}


def infer_page(path: str, data: Any, image_dirs: Callable[[str], str]) -> tuple[dict | None, list[str]]:
    """Schema page guessed from a content file; returns (page or None, notes about skipped keys)."""
    if not isinstance(data, dict) or not data:
        return None, [f"{path}: not a JSON object, skipped"]
    fields, notes = [], []
    for key, value in data.items():
        if not KEY_RE.fullmatch(str(key)):
            notes.append(f"{path} › {key}: unusual key name, left out")
            continue
        if isinstance(value, list):
            items = [v for v in value if isinstance(v, dict)]
            if not value or len(items) != len(value):
                notes.append(f"{path} › {key}: list of plain values, left out (lists must contain objects)")
                continue
            subs: dict[str, dict] = {}
            for item in items:
                for k, v in item.items():
                    if k in subs or not KEY_RE.fullmatch(str(k)):
                        continue
                    f = _infer_scalar(k, v, image_dirs)
                    if f is None:
                        notes.append(f"{path} › {key} › {k}: nested data, left out")
                    else:
                        subs[k] = f
            for k, f in subs.items():  # longest value decides the size of text fields
                lengths = [len(it[k]) for it in items if isinstance(it.get(k), str)]
                if f["type"] in DEFAULT_MAX and lengths:
                    floor, ceiling = {"text": (80, 200), "textarea": (500, 5000), "markdown": (2000, 20000)}[f["type"]]
                    f["max"] = _cap(max(lengths), floor, ceiling)
            if subs:
                fields.append({"key": key, "label": humanize(key), "type": "list", "item_label": humanize(key).rstrip("s") or "Item",
                               "max_items": max(10, min(MAX_ITEMS, len(items) * 2)), "fields": list(subs.values())})
            continue
        field = _infer_scalar(key, value, image_dirs)
        if field is None:
            notes.append(f"{path} › {key}: nested object, left out")
        else:
            fields.append(field)
    if not fields:
        return None, notes + [f"{path}: nothing editable found"]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", PurePosixPath(path).stem).strip("-") or "page"
    return {"id": stem, "label": humanize(stem), "file": path, "fields": fields[:MAX_FIELDS]}, notes


def infer_schema(contents: dict[str, Any], image_dirs: Callable[[str], str]) -> tuple[dict, list[str]]:
    pages, notes, ids = [], [], set()
    for path, data in contents.items():
        page, page_notes = infer_page(path, data, image_dirs)
        notes += page_notes
        if page is None:
            continue
        base, n = page["id"], 2
        while page["id"] in ids:  # content/fr/home.json and content/en/home.json
            page["id"] = f"{base}-{n}"
            n += 1
        ids.add(page["id"])
        pages.append(page)
    if not pages:
        raise CmsError("no editable content found in the selected files")
    schema = {"pages": pages}
    parse_schema(json.dumps(schema))  # what we generate must be accepted
    return schema, notes


# -- manager -------------------------------------------------------------------


class CmsManager:
    def __init__(self, db: Database, github: Callable[[], GitHubApp | None], deployer, data_dir: str | Path,
                 supabase=None) -> None:
        self.db = db
        self.github = github
        self.deployer = deployer
        self.supabase = supabase  # SupabaseManager: the instance of this server, if any
        self.supabase_opener: cms_supabase.Opener = cms_supabase._urllib_opener
        self.staging = Path(data_dir) / "cms-uploads"
        self._locks: dict[str, threading.Lock] = {}
        self._schemas: dict[str, tuple[float, str, dict]] = {}
        self._compat: dict[str, tuple[float, dict]] = {}

    def _store(self, app: dict) -> cms_supabase.SupabaseStore:
        """Supabase of the app: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in its environment, else this server's."""
        try:
            env = decrypt_env(load_fernet(self.deployer.host, self.deployer.config.secret_key_path), app["env"])
        except EnvError:
            env = {}
        inst = self.supabase.get() if self.supabase else None
        local = inst if inst and inst["status"] == "running" and inst["secrets"] else None
        loopback = f"http://127.0.0.1:{self.supabase.ports(local).get('kong_http')}" if local else None
        public = self.supabase.api_url(local).rstrip("/") if local else None
        url, key = (env.get("SUPABASE_URL") or "").rstrip("/"), env.get("SUPABASE_SERVICE_ROLE_KEY")
        if url and key:
            is_local = url == public
            store = cms_supabase.SupabaseStore(loopback if is_local else url, url, key, self.supabase_opener)
        elif local:
            is_local = True
            store = cms_supabase.SupabaseStore(loopback, public, self.supabase.secrets(local)["SERVICE_ROLE_KEY"],
                                               self.supabase_opener)
        else:
            raise CmsError("this collection is stored in Supabase: deploy Supabase (Databases) and connect it to "
                           "the app, or set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in its environment")
        store.local = is_local  # type: ignore[attr-defined]
        return store

    def _consume(self, uploads: list[str]) -> None:
        for token in uploads:
            (self.staging / f"{token}.bin").unlink(missing_ok=True)
            (self.staging / f"{token}.json").unlink(missing_ok=True)

    def _target(self, app_id: str) -> tuple[GitHubApp, dict]:
        app = self.db.get_app(app_id)
        if app is None:
            raise CmsError(f"unknown app {app_id}")
        gh = self.github()
        if gh is None:
            raise CmsError("GitHub is not connected (Settings → GitHub)")
        if not app["installation_id"] or not app["repo"]:
            raise CmsError("this app is not linked to a GitHub repository")
        return gh, app

    @staticmethod
    def _github_error(exc: GitHubError) -> CmsError:
        text = str(exc)
        if "HTTP 403" in text or "HTTP 404" in text:
            return CmsError("the GitHub App cannot write to this repository: give it Contents: Read and write "
                            "(GitHub → Settings → Developer settings → GitHub Apps → Permissions) and accept "
                            "the new permissions on the installation")
        if "HTTP 422" in text or "HTTP 409" in text:
            return CmsConflict("the site changed on GitHub meanwhile: reload and try again")
        return CmsError(text)

    def schema(self, app_id: str) -> tuple[dict, str]:
        """(parsed schema, head sha); cached per head for a minute."""
        gh, app = self._target(app_id)
        try:
            head = gh.head(app["installation_id"], app["repo"], app["branch"])
            cached = self._schemas.get(app_id)
            if cached and cached[1] == head and cached[0] > time.time():
                return cached[2], head
            raw, _ = gh.read_file(app["installation_id"], app["repo"], SCHEMA_FILE, head)
        except GitHubError as exc:
            raise CmsError(str(exc)) from None
        if raw is None:
            raise CmsError(f"no {SCHEMA_FILE} at the root of {app['repo']}@{app['branch']}")
        schema = parse_schema(raw)

        def read_page(page: dict) -> dict | None:
            try:
                content, _ = gh.read_file(app["installation_id"], app["repo"], page["file"], head)
            except GitHubError as exc:
                raise CmsError(str(exc)) from None
            return self._decode(content, page["file"]) if content is not None else None

        schema = resolve_options(schema, read_page)
        self._schemas[app_id] = (time.time() + 60, head, schema)
        return schema, head

    def check(self, app_id: str) -> dict:
        """Admin view: is the CMS usable on this app?"""
        out: dict = {"schema_file": SCHEMA_FILE, "ok": False, "error": None, "pages": [], "collections": [],
                     "can_write": None}
        try:
            gh, app = self._target(app_id)
            out["can_write"] = gh.permissions(app["installation_id"]).get("contents") == "write"
            schema, _ = self.schema(app_id)
            out["pages"] = [{"id": p["id"], "label": p["label"], "file": p["file"], "fields": len(p["fields"])}
                            for p in schema["pages"]]
            out["collections"] = [self._check_collection(app, c) for c in schema["collections"]]
            out["ok"] = True
        except (CmsError, GitHubError) as exc:
            out["error"] = str(exc)
        return out

    def load(self, app_id: str) -> dict:
        gh, app = self._target(app_id)
        schema, head = self.schema(app_id)
        pages = []
        for page in schema["pages"]:
            try:
                raw, sha = gh.read_file(app["installation_id"], app["repo"], page["file"], head)
            except GitHubError as exc:
                raise CmsError(str(exc)) from None
            values = self._decode(raw, page["file"])
            pages.append({**page, "values": {f["key"]: values.get(f["key"], _empty(f)) for f in page["fields"]},
                          "sha": sha})
        collections = []
        for c in schema["collections"]:
            if c["storage"] == "git":
                collections.append({**c, "entries": self._entries(gh, app, c, head)[1], "error": None})
                continue
            try:  # a missing table must not hide the pages
                collections.append({**c, "entries": self._store(app).list(c), "error": None})
            except CmsError as exc:
                collections.append({**c, "entries": [], "error": str(exc)})
        return {"head": head, "pages": pages, "collections": collections}

    def _check_collection(self, app: dict, c: dict) -> dict:
        out = {"id": c["id"], "label": c["label"], "storage": c["storage"], "dir": c["dir"], "table": c["table"],
               "fields": len(c["fields"]), "error": None}
        if c["storage"] == "supabase":
            out.update(sql=cms_supabase.table_sql(c), local=False, url=None)
            try:
                store = self._store(app)
                out.update(local=store.local, url=store.public)  # type: ignore[attr-defined]
                store.list(c, limit=1)
            except CmsError as exc:
                out["error"] = str(exc)
        return out

    def create_table(self, app_id: str, cid: str) -> dict:
        """Create or complete the table of a Supabase collection, on the instance of this server."""
        gh, app = self._target(app_id)
        c = self._collection(self.schema(app_id)[0], cid)
        if c["storage"] != "supabase":
            raise CmsError(f"{cid} is stored in the repository, not in Supabase")
        if not self._store(app).local:  # type: ignore[attr-defined]
            raise CmsError("this app uses its own Supabase project: run the SQL in its SQL editor")
        try:
            self.supabase.run_sql(cms_supabase.table_sql(c))
        except SupabaseError as exc:
            raise CmsError(str(exc)) from None
        return self._check_collection(app, c)

    @staticmethod
    def _decode(raw: bytes | None, path: str) -> dict:
        if raw is None:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise CmsError(f"{path} is not valid JSON: fix it in the repository first") from None
        if not isinstance(data, dict):
            raise CmsError(f"{path} must contain a JSON object")
        return data

    # -- uploads ---------------------------------------------------------------

    def stage_upload(self, app_id: str, user_id: int, name: str, data: bytes) -> str:
        if len(data) > MAX_UPLOAD_KB * 1024:
            raise CmsError(f"image larger than {MAX_UPLOAD_KB // 1024} MB")
        ext = detect_image(data)
        if ext is None:
            raise CmsError("only PNG, JPEG, GIF, WebP and AVIF images are accepted")
        self.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = time.time()
        for old in self.staging.glob("*.json"):
            if old.stat().st_mtime < now - UPLOAD_TTL:
                old.unlink(missing_ok=True)
                old.with_suffix(".bin").unlink(missing_ok=True)
        stem = re.sub(r"[^a-z0-9]+", "-", PurePosixPath(name or "image").stem.lower()).strip("-")[:40] or "image"
        token = secrets.token_hex(16)
        (self.staging / f"{token}.bin").write_bytes(data)
        (self.staging / f"{token}.json").write_text(json.dumps(
            {"app_id": app_id, "user_id": user_id, "ext": ext, "stem": stem}), encoding="utf-8")
        return token

    def _resolver(self, app_id: str, user_id: int) -> Callable[[str], tuple[bytes, str, str]]:
        def resolve(token: str) -> tuple[bytes, str, str]:
            meta_path = self.staging / f"{token}.json"
            if not re.fullmatch(r"[0-9a-f]{32}", token) or not meta_path.is_file():
                raise CmsError("an uploaded image expired: upload it again")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["app_id"] != app_id or meta["user_id"] != user_id:
                raise CmsError("an uploaded image expired: upload it again")
            return (self.staging / f"{token}.bin").read_bytes(), meta["ext"], meta["stem"]
        return resolve

    def read_image(self, app_id: str, src: str) -> tuple[bytes, str]:
        """Preview of an image the site already has (``src`` as stored in the content)."""
        gh, app = self._target(app_id)
        schema, head = self.schema(app_id)
        for field in image_fields(schema):
            prefix = field["url"].rstrip("/") + "/"
            rest = src[len(prefix):] if src.startswith(prefix) else None
            if rest and FILENAME_RE.fullmatch(rest):
                try:
                    data, _ = gh.read_file(app["installation_id"], app["repo"], f"{field['dir']}/{rest}", head)
                except GitHubError as exc:
                    raise CmsError(str(exc)) from None
                ext = detect_image(data or b"")
                if data and ext:
                    return data, IMAGE_MIME[ext]
        for c in (c for c in schema["collections"] if c["storage"] == "supabase"):
            store = self._store(app)
            prefix = store.public_prefix(c["id"]) + "/"
            rest = src[len(prefix):] if src.startswith(prefix) else None
            if rest and FILENAME_RE.fullmatch(rest):
                data = store.download(f"{c['id']}/{rest}")
                ext = detect_image(data or b"")
                if data and ext:
                    return data, IMAGE_MIME[ext]
        raise CmsError("image not found")

    # -- save ------------------------------------------------------------------

    def save(self, app_id: str, page_id: str, values: dict, base_sha: str | None, user_id: int,
             username: str, note: str = "") -> dict:
        lock = self._locks.setdefault(app_id, threading.Lock())
        with lock:
            gh, app = self._target(app_id)
            schema, head = self.schema(app_id)  # from the repository, never from the client
            page = next((p for p in schema["pages"] if p["id"] == page_id), None)
            if page is None:
                raise CmsError(f"unknown page {page_id}")
            try:
                raw, sha = gh.read_file(app["installation_id"], app["repo"], page["file"], head)
            except GitHubError as exc:
                raise CmsError(str(exc)) from None
            if sha != base_sha:
                raise CmsConflict("this page changed since you opened it: reload to get the latest version")
            existing = self._decode(raw, page["file"])
            merged, files, uploads = apply_values(page, values, existing, self._resolver(app_id, user_id))
            content = dump_json(merged)
            if content == raw and not files:
                return {"commit": None, "unchanged": True, "release_id": None}
            files[page["file"]] = content
            return self._commit(gh, app, files, f"cms({page_id})", note, f"update {page['label']}", username, head,
                                uploads)

    def _commit(self, gh: GitHubApp, app: dict, files: dict[str, bytes | None], scope: str, note: str,
                default: str, username: str, head: str, uploads: list[str]) -> dict:
        note = " ".join((note or "").split())[:100]
        message = f"{scope}: {note or default}\n\nEdited by {username} with Mas CMS."
        try:
            commit = gh.commit_files(app["installation_id"], app["repo"], app["branch"], files, message, head)
        except GitHubError as exc:
            raise self._github_error(exc) from None
        self._consume(uploads)
        self._schemas.pop(app["id"], None)
        release_id = None
        if not app["auto_deploy"]:  # otherwise the push webhook deploys it
            release_id = self.deployer.enqueue(app["id"], "cms", username, commit)
        return {"commit": commit, "unchanged": False, "release_id": release_id}

    # -- collections -------------------------------------------------------------

    @staticmethod
    def _collection(schema: dict, cid: str) -> dict:
        found = next((c for c in schema["collections"] if c["id"] == cid), None)
        if found is None:
            raise CmsError(f"unknown collection {cid}")
        return found

    def _entries(self, gh: GitHubApp, app: dict, collection: dict, head: str) -> tuple[dict[str, str], list[dict]]:
        """(slug → blob sha, index). Entries added by hand in the repository are read and indexed too."""
        folder, ref = collection["dir"], (app["installation_id"], app["repo"])
        try:
            files = {f["name"][:-5]: f["sha"] for f in gh.list_dir(*ref, folder, head)
                     if f["name"].endswith(".json") and SLUG_RE.fullmatch(f["name"][:-5]) and f["name"] != INDEX_FILE}
            raw, _ = gh.read_file(*ref, f"{folder}/{INDEX_FILE}", head)
            try:
                listed = json.loads(raw) if raw else []
            except ValueError:
                listed = []
            index = {e["slug"]: e for e in (listed if isinstance(listed, list) else [])
                     if isinstance(e, dict) and e.get("slug") in files}
            for slug in sorted(files.keys() - index.keys()):
                data, _ = gh.read_file(*ref, f"{folder}/{slug}.json", head)
                try:
                    index[slug] = index_entry(collection, slug, self._decode(data, slug))
                except CmsError:
                    index[slug] = {"slug": slug}
        except GitHubError as exc:
            raise CmsError(str(exc)) from None
        return files, sort_index(collection, list(index.values()))

    def load_entry(self, app_id: str, cid: str, slug: str) -> dict:
        gh, app = self._target(app_id)
        schema, head = self.schema(app_id)
        c = self._collection(schema, cid)
        if not SLUG_RE.fullmatch(slug):
            raise CmsError("unknown entry")
        if c["storage"] == "supabase":
            row = self._store(app).get(c, slug)
            if row is None:
                raise CmsError(f"{slug} does not exist anymore: reload the list")
            return {"slug": slug, "values": {f["key"]: _empty(f) if row.get(f["key"]) is None else row[f["key"]]
                                             for f in c["fields"]}, "sha": row["updated_at"]}
        try:
            raw, sha = gh.read_file(app["installation_id"], app["repo"], f"{c['dir']}/{slug}.json", head)
        except GitHubError as exc:
            raise CmsError(str(exc)) from None
        if raw is None:
            raise CmsError(f"{slug} does not exist anymore: reload the list")
        values = self._decode(raw, f"{c['dir']}/{slug}.json")
        return {"slug": slug, "values": {f["key"]: values.get(f["key"], _empty(f)) for f in c["fields"]}, "sha": sha}

    def save_entry(self, app_id: str, cid: str, slug: str | None, values: dict, base_sha: str | None, user_id: int,
                   username: str, note: str = "", new_slug: str | None = None) -> dict:
        """Create (``slug`` None, named ``new_slug`` or after its title) or update an entry, with the index."""
        lock = self._locks.setdefault(app_id, threading.Lock())
        with lock:
            gh, app = self._target(app_id)
            schema, head = self.schema(app_id)
            c = self._collection(schema, cid)
            if slug is None:
                if new_slug and (len(new_slug) > SLUG_MAX or not SLUG_RE.fullmatch(new_slug)):
                    raise CmsError("address: lowercase letters, digits and dashes (ete-a-paris)")
                if isinstance(values, dict):  # a new entry has every field, so required ones are checked
                    values = {**{f["key"]: None for f in c["fields"]}, **values}
            if c["storage"] == "supabase":
                return self._save_row(app, c, slug, values, base_sha, user_id, new_slug)
            files, index = self._entries(gh, app, c, head)
            raw, existing = None, {}
            if slug is None:
                if len(files) >= c["max_entries"]:
                    raise CmsError(f"{c['label']}: at most {c['max_entries']} entries")
            else:
                if slug not in files:
                    raise CmsConflict(f"{slug} was deleted meanwhile: reload the list")
                try:
                    raw, sha = gh.read_file(app["installation_id"], app["repo"], f"{c['dir']}/{slug}.json", head)
                except GitHubError as exc:
                    raise CmsError(str(exc)) from None
                if sha != base_sha:
                    raise CmsConflict("this entry changed since you opened it: reload to get the latest version")
                existing = self._decode(raw, slug)
            merged, new_files, uploads = apply_values(c, values, existing, self._resolver(app_id, user_id))
            title = merged[c["title_field"]]
            if slug is None:
                slug = self._new_slug(new_slug or slugify(title), set(files), bool(new_slug))
            content = dump_json({"slug": slug, **{k: v for k, v in merged.items() if k != "slug"}})
            if content == raw and not new_files:
                return {"slug": slug, "commit": None, "unchanged": True, "release_id": None}
            entries = [e for e in index if e["slug"] != slug] + [index_entry(c, slug, merged)]
            new_files[f"{c['dir']}/{slug}.json"] = content
            new_files[f"{c['dir']}/{INDEX_FILE}"] = dump_json(sort_index(c, entries))
            verb = "add" if raw is None else "update"
            result = self._commit(gh, app, new_files, f"cms({cid})", note, f"{verb} {title}", username, head, uploads)
            return {"slug": slug, **result}

    @staticmethod
    def _new_slug(base: str, taken: set[str], chosen: bool) -> str:
        if chosen and base in taken:
            raise CmsError(f"{base} is already used: choose another address")
        slug, n = base, 2
        while slug in taken or slug == "index":
            slug, n = f"{base[:SLUG_MAX - 4]}-{n}", n + 1
        return slug

    def _save_row(self, app: dict, c: dict, slug: str | None, values: dict, version: str | None, user_id: int,
                  new_slug: str | None) -> dict:
        """Supabase collection: images go to Storage, the entry is one row, online at once (no commit, no build)."""
        store = self._store(app)
        prefix = store.public_prefix(c["id"])
        stored = {**c, "fields": [cms_supabase.media_field(f, prefix, c["id"]) for f in c["fields"]]}
        existing: dict = {}
        if slug is None:
            taken = store.slugs(c)
            if len(taken) >= c["max_entries"]:
                raise CmsError(f"{c['label']}: at most {c['max_entries']} entries")
        else:
            row = store.get(c, slug)
            if row is None:
                raise CmsConflict(f"{slug} was deleted meanwhile: reload the list")
            if row["updated_at"] != version:
                raise CmsConflict("this entry changed since you opened it: reload to get the latest version")
            existing = {k: v for k, v in row.items() if k not in cms_supabase.RESERVED and v is not None}
        merged, files, uploads = apply_values(stored, values, existing, self._resolver(app["id"], user_id))
        data = {f["key"]: merged.get(f["key"]) for f in c["fields"]}
        if slug is not None and not files and data == {f["key"]: existing.get(f["key"]) for f in c["fields"]}:
            return {"slug": slug, "commit": None, "unchanged": True, "release_id": None, "instant": True}
        for path, content in files.items():
            store.upload(path, content, IMAGE_MIME[PurePosixPath(path).suffix[1:]])
        if slug is None:
            slug = self._new_slug(new_slug or slugify(merged[c["title_field"]]), taken, bool(new_slug))
            store.insert(c, {"slug": slug, **data})
        else:
            store.update(c, slug, data, version)
        self._consume(uploads)
        return {"slug": slug, "commit": None, "unchanged": False, "release_id": None, "instant": True}

    def delete_entry(self, app_id: str, cid: str, slug: str, base_sha: str | None, username: str) -> dict:
        lock = self._locks.setdefault(app_id, threading.Lock())
        with lock:
            gh, app = self._target(app_id)
            schema, head = self.schema(app_id)
            c = self._collection(schema, cid)
            if c["storage"] == "supabase":
                self._store(app).delete(c, slug, base_sha)
                return {"commit": None, "unchanged": False, "release_id": None, "instant": True}
            files, index = self._entries(gh, app, c, head)
            if slug not in files:
                raise CmsConflict(f"{slug} was already deleted: reload the list")
            if files[slug] != base_sha:
                raise CmsConflict("this entry changed since you opened it: reload to get the latest version")
            title = next((e.get(c["title_field"]) for e in index if e["slug"] == slug), None) or slug
            changes: dict[str, bytes | None] = {
                f"{c['dir']}/{slug}.json": None,
                f"{c['dir']}/{INDEX_FILE}": dump_json([e for e in index if e["slug"] != slug])}
            return self._commit(gh, app, changes, f"cms({cid})", "", f"delete {title}", username, head, [])

    # -- compatibility (repository list) -----------------------------------------

    def compatibility(self, repos: list[dict]) -> dict[str, dict]:
        """Which repositories are ready for the CMS: a valid mas.cms.json on their default branch."""
        gh = self.github()
        if gh is None:
            raise CmsError("GitHub is not connected (Settings → GitHub)")

        def one(r: dict) -> tuple[str, dict]:
            key = f"{r['full_name']}@{r['default_branch']}"
            cached = self._compat.get(key)
            if cached and cached[0] > time.time():
                return r["full_name"], cached[1]
            out: dict = {"compatible": False, "pages": 0, "collections": 0, "storage": [], "error": None}
            try:
                raw, _ = gh.read_file(r["installation_id"], r["full_name"], SCHEMA_FILE, r["default_branch"])
                if raw is not None:
                    schema = parse_schema(raw)
                    out.update(compatible=True, pages=len(schema["pages"]), collections=len(schema["collections"]),
                               storage=sorted({c["storage"] for c in schema["collections"]}))
            except CmsError as exc:
                out["error"] = str(exc)
            except GitHubError:
                return r["full_name"], out  # not cached: GitHub may answer next time
            self._compat[key] = (time.time() + COMPAT_TTL, out)
            return r["full_name"], out

        with ThreadPoolExecutor(max_workers=8) as pool:
            return dict(pool.map(one, repos))

    # -- schema generation (administrators) ------------------------------------

    def candidates(self, app_id: str) -> dict:
        gh, app = self._target(app_id)
        try:
            head = gh.head(app["installation_id"], app["repo"], app["branch"])
            files = gh.tree(app["installation_id"], app["repo"], head)
        except GitHubError as exc:
            raise CmsError(str(exc)) from None
        entries = tuple(f"{d}/" for d in self._kept_collections(gh, app, head)[1])  # articles are not pages
        return {"files": content_candidates([f for f in files if not f["path"].startswith(entries)]),
                "has_schema": any(f["path"] == SCHEMA_FILE for f in files)}

    @staticmethod
    def _kept_collections(gh: GitHubApp, app: dict, head: str) -> tuple[list, list[str]]:
        """Collections of the current schema as written (kept when the pages are regenerated), and their folders."""
        try:
            raw, _ = gh.read_file(app["installation_id"], app["repo"], SCHEMA_FILE, head)
            kept = (json.loads(raw) if raw else {}).get("collections") or []
            parsed = parse_schema(json.dumps({"collections": kept}))["collections"]
            return kept, [c["dir"] for c in parsed if c["dir"]]
        except (GitHubError, CmsError, ValueError, AttributeError):
            return [], []

    def _image_dirs(self, app: dict) -> Callable[[str], str]:
        # Static and PHP sites serve their (sub-)folder as is; JS frameworks serve public/.
        root = (app.get("root_dir") or "").strip("/") if app["type"] in ("static", "php") else "public"

        def resolve(url: str) -> str:
            return "/".join(p for p in (root, url.strip("/")) if p) or "uploads"
        return resolve

    def generate(self, app_id: str, paths: list[str]) -> dict:
        gh, app = self._target(app_id)
        if not paths:
            raise CmsError("select at least one file")
        try:
            head = gh.head(app["installation_id"], app["repo"], app["branch"])
            contents = {}
            for path in paths[:MAX_PAGES]:
                safe_repo_path(path, path)
                raw, _ = gh.read_file(app["installation_id"], app["repo"], path, head)
                if raw is None:
                    raise CmsError(f"{path} does not exist")
                try:
                    contents[path] = json.loads(raw)
                except ValueError:
                    raise CmsError(f"{path} is not valid JSON") from None
        except GitHubError as exc:
            raise CmsError(str(exc)) from None
        schema, notes = infer_schema(contents, self._image_dirs(app))
        schema = {"$schema": SCHEMA_URL, **schema}
        kept, _ = self._kept_collections(gh, app, head)
        if kept:
            schema["collections"] = kept
        return {"schema": schema, "notes": notes}

    def write_schema(self, app_id: str, schema: dict, username: str) -> dict:
        gh, app = self._target(app_id)
        content = dump_json(schema)
        parse_schema(content)  # refuse an invalid file
        lock = self._locks.setdefault(app_id, threading.Lock())
        with lock:
            try:
                head = gh.head(app["installation_id"], app["repo"], app["branch"])
                commit = gh.commit_files(app["installation_id"], app["repo"], app["branch"], {SCHEMA_FILE: content},
                                         f"cms: {SCHEMA_FILE}\n\nWritten by {username} with Mas.", head)
            except GitHubError as exc:
                raise self._github_error(exc) from None
            self._schemas.pop(app_id, None)
        return {"commit": commit}  # the site does not read the schema: nothing to redeploy
