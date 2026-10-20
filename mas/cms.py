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
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from mas.db import Database
from mas.github import GitHubApp, GitHubError

log = logging.getLogger(__name__)

SCHEMA_FILE = "mas.cms.json"
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
MAX_UPLOAD_KB = 8192
UPLOAD_TTL = 24 * 3600
FORBIDDEN_DIRS = (".git", ".github")
IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              "avif": "image/avif"}


class CmsError(Exception):
    pass


class CmsConflict(CmsError):
    """The content changed on GitHub since the editor opened it."""


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
        if not options:
            raise CmsError(f"{where}: a select needs options")
        field["options"] = options
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


def parse_schema(raw: bytes | str) -> dict:
    """Validate ``mas.cms.json``; returns a normalized copy or raises CmsError."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CmsError(f"{SCHEMA_FILE} is not valid JSON: {exc}") from None
    pages_raw = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages_raw, list) or not pages_raw:
        raise CmsError(f'{SCHEMA_FILE} needs a non-empty "pages" list')
    if len(pages_raw) > MAX_PAGES:
        raise CmsError(f"at most {MAX_PAGES} pages")
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
        fields_raw = p.get("fields")
        if not isinstance(fields_raw, list) or not fields_raw or len(fields_raw) > MAX_FIELDS:
            raise CmsError(f"{pid}: needs between 1 and {MAX_FIELDS} fields")
        fields = [_parse_field(f, pid, nested=False) for f in fields_raw]
        _unique([f["key"] for f in fields], pid)
        pages.append({"id": pid, "label": str(p.get("label") or pid)[:100],
                      "description": str(p.get("description") or "")[:500], "file": file, "fields": fields})
    _unique([p["id"] for p in pages], "pages")
    _unique([p["file"] for p in pages], "page files")
    return {"pages": pages}


def image_fields(schema: dict) -> list[dict]:
    out = []
    for page in schema["pages"]:
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


def dump_json(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


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
    def __init__(self, db: Database, github: Callable[[], GitHubApp | None], deployer, data_dir: str | Path) -> None:
        self.db = db
        self.github = github
        self.deployer = deployer
        self.staging = Path(data_dir) / "cms-uploads"
        self._locks: dict[str, threading.Lock] = {}
        self._schemas: dict[str, tuple[float, str, dict]] = {}

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
        self._schemas[app_id] = (time.time() + 60, head, schema)
        return schema, head

    def check(self, app_id: str) -> dict:
        """Admin view: is the CMS usable on this app?"""
        out: dict = {"schema_file": SCHEMA_FILE, "ok": False, "error": None, "pages": [], "can_write": None}
        try:
            gh, app = self._target(app_id)
            out["can_write"] = gh.permissions(app["installation_id"]).get("contents") == "write"
            schema, _ = self.schema(app_id)
            out["pages"] = [{"id": p["id"], "label": p["label"], "file": p["file"], "fields": len(p["fields"])}
                            for p in schema["pages"]]
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
        return {"head": head, "pages": pages}

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
            note = " ".join((note or "").split())[:100]
            message = f"cms({page_id}): {note or 'update ' + page['label']}\n\nEdited by {username} with Mas CMS."
            try:
                commit = gh.commit_files(app["installation_id"], app["repo"], app["branch"], files, message, head)
            except GitHubError as exc:
                raise self._github_error(exc) from None
            for token in uploads:
                (self.staging / f"{token}.bin").unlink(missing_ok=True)
                (self.staging / f"{token}.json").unlink(missing_ok=True)
            self._schemas.pop(app_id, None)
            release_id = None
            if not app["auto_deploy"]:  # otherwise the push webhook deploys it
                release_id = self.deployer.enqueue(app_id, "cms", username, commit)
            return {"commit": commit, "unchanged": False, "release_id": release_id}

    # -- schema generation (administrators) ------------------------------------

    def candidates(self, app_id: str) -> dict:
        gh, app = self._target(app_id)
        try:
            head = gh.head(app["installation_id"], app["repo"], app["branch"])
            files = gh.tree(app["installation_id"], app["repo"], head)
        except GitHubError as exc:
            raise CmsError(str(exc)) from None
        return {"files": content_candidates(files), "has_schema": any(f["path"] == SCHEMA_FILE for f in files)}

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
