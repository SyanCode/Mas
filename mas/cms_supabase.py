"""Collections stored in Supabase: one row per entry (PostgREST), images in Storage.

A collection opts in with ``"storage": "supabase"``. Its table has one column
per field, plus ``slug``, ``created_at`` and ``updated_at``; row level security
lets anyone read it and only the service_role key write it. The site reads the
table at runtime with its anon key, so a change is online at once, without a
rebuild. ``updated_at`` is the version an editor started from: a write that
does not match it is refused instead of overwriting someone else's change.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from mas.cms import CmsConflict, CmsError

COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
RESERVED = {"slug", "created_at", "updated_at"}
BUCKET = "mas-cms"
SQL_TYPES = {"number": "double precision", "boolean": "boolean", "date": "date", "list": "jsonb"}

Opener = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


def _urllib_opener(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CmsError(f"cannot reach Supabase: {getattr(exc, 'reason', exc)}") from exc


def column_type(field: dict) -> str:
    if field["type"] == "number" and field.get("integer"):
        return "bigint"
    return SQL_TYPES.get(field["type"], "text")


def table_sql(collection: dict) -> str:
    """SQL creating (or completing, when fields were added) the table of a collection."""
    t = collection["table"]
    columns = ",\n".join(["  slug text primary key",
                          *(f"  {f['key']} {column_type(f)}" for f in collection["fields"]),
                          "  created_at timestamptz not null default now()",
                          "  updated_at timestamptz not null default now()"])
    added = "".join(f"alter table public.{t} add column if not exists {f['key']} {column_type(f)};\n"
                    for f in collection["fields"])
    return (f"create table if not exists public.{t} (\n{columns}\n);\n{added}"
            f"alter table public.{t} enable row level security;\n"
            f'drop policy if exists "{t} are public" on public.{t};\n'
            f'create policy "{t} are public" on public.{t} for select to anon, authenticated using (true);\n'
            "notify pgrst, 'reload schema';\n")


def media_field(field: dict, public_prefix: str, folder: str) -> dict:
    """Image fields of a Supabase collection upload to Storage instead of the repository."""
    if field["type"] == "image":
        return {**field, "dir": folder, "url": public_prefix}
    if field["type"] == "list":
        return {**field, "fields": [media_field(f, public_prefix, folder) for f in field["fields"]]}
    return field


class SupabaseStore:
    def __init__(self, api_url: str, public_url: str, service_key: str, opener: Opener = _urllib_opener) -> None:
        self.api = api_url.rstrip("/")
        self.public = public_url.rstrip("/")
        self.key = service_key
        self.opener = opener

    def _call(self, method: str, path: str, body: Any = None, headers: dict | None = None,
              data: bytes | None = None) -> tuple[int, bytes]:
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}", **(headers or {})}
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        return self.opener(method, self.api + path, h, data)

    @staticmethod
    def _json(payload: bytes) -> Any:
        try:
            return json.loads(payload) if payload else None
        except ValueError:
            return None

    @staticmethod
    def _fail(collection: dict, status: int, payload: Any) -> CmsError:
        info = payload if isinstance(payload, dict) else {}
        message = str(info.get("message") or info.get("error") or "")
        if info.get("code") in ("42P01", "PGRST205") or "does not exist" in message or "schema cache" in message:
            return CmsError(f"the table {collection['table']} does not exist yet in Supabase: create it from the "
                            "CMS card of the app")
        if info.get("code") == "23505":
            return CmsConflict("this address is already used: choose another one")
        if status in (401, 403):
            return CmsError("Supabase refused the service_role key of this app (SUPABASE_SERVICE_ROLE_KEY)")
        return CmsError(f"Supabase: HTTP {status} {message}".strip())

    def _rows(self, collection: dict, method: str, query: str, body: Any = None) -> list[dict]:
        headers = {"Prefer": "return=representation"} if method != "GET" else {}
        status, payload = self._call(method, f"/rest/v1/{collection['table']}?{query}", body, headers)
        data = self._json(payload)
        if status >= 400:
            raise self._fail(collection, status, data)
        return data if isinstance(data, list) else []

    # -- rows ----------------------------------------------------------------------

    def list(self, collection: dict, limit: int | None = None) -> list[dict]:
        columns = ",".join(["slug", *collection["index_fields"]])
        order = (f"{collection['sort_field']}.desc.nullslast," if collection["sort_field"] else "") + "slug.asc"
        return self._rows(collection, "GET",
                          f"select={columns}&order={order}&limit={limit or collection['max_entries']}")

    def slugs(self, collection: dict) -> set[str]:
        return {r["slug"] for r in self._rows(collection, "GET", f"select=slug&limit={collection['max_entries'] + 1}")}

    def get(self, collection: dict, slug: str) -> dict | None:
        rows = self._rows(collection, "GET", f"select=*&slug=eq.{quote(slug)}")
        return rows[0] if rows else None

    def insert(self, collection: dict, row: dict) -> dict:
        rows = self._rows(collection, "POST", "select=*", row)
        if not rows:
            raise CmsError("Supabase did not return the new entry")
        return rows[0]

    def update(self, collection: dict, slug: str, row: dict, version: str | None) -> dict:
        row = {**row, "updated_at": datetime.now(timezone.utc).isoformat()}
        rows = self._rows(collection, "PATCH", f"slug=eq.{quote(slug)}&updated_at=eq.{quote(version or '')}", row)
        if not rows:
            raise CmsConflict("this entry changed since you opened it: reload to get the latest version")
        return rows[0]

    def delete(self, collection: dict, slug: str, version: str | None) -> None:
        if not self._rows(collection, "DELETE", f"slug=eq.{quote(slug)}&updated_at=eq.{quote(version or '')}"):
            raise CmsConflict("this entry changed since you opened it: reload to get the latest version")

    # -- storage -------------------------------------------------------------------

    def public_prefix(self, folder: str) -> str:
        return f"{self.public}/storage/v1/object/public/{BUCKET}/{folder}"

    def upload(self, path: str, data: bytes, mime: str) -> None:
        headers = {"Content-Type": mime, "x-upsert": "false", "Cache-Control": "max-age=31536000"}
        for attempt in (1, 2):
            status, payload = self._call("POST", f"/storage/v1/object/{BUCKET}/{path}", headers=headers, data=data)
            if status < 400:
                return
            info = self._json(payload) if isinstance(self._json(payload), dict) else {}
            if attempt == 1 and (status == 404 or "not found" in str(info.get("message", "")).lower()):
                self._call("POST", "/storage/v1/bucket", {"id": BUCKET, "name": BUCKET, "public": True})
                continue  # first image: the public bucket did not exist yet
            raise CmsError(f"Supabase Storage: HTTP {status} {info.get('message') or info.get('error') or ''}".strip())

    def download(self, path: str) -> bytes | None:
        status, payload = self._call("GET", f"/storage/v1/object/{BUCKET}/{path}")
        return payload if status < 400 else None
