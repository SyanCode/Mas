"""API routes of the CMS: editor accounts, per-app access, and the editing endpoints."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mas import auth
from mas.cms import MAX_UPLOAD_KB, CmsConflict, CmsError, CmsManager
from mas.db import Database
from mas.sitestats import SiteStats


class NewUser(BaseModel):
    username: str = Field(max_length=32)


class AppIds(BaseModel):
    apps: list[str] = Field(max_length=500)


class UserIds(BaseModel):
    user_ids: list[int] = Field(max_length=500)


class InviteBody(BaseModel):
    apps: list[str] = Field(default_factory=list, max_length=500)
    note: str = Field(default="", max_length=100)  # who it is for, shown in the list
    days: int = Field(default=7, ge=1, le=30)


class Paths(BaseModel):
    files: list[str] = Field(max_length=50)


class SchemaBody(BaseModel):
    schema_: dict = Field(alias="schema")


class SaveBody(BaseModel):
    values: dict
    sha: str | None = Field(default=None, max_length=64)  # blob sha the editor started from
    note: str = Field(default="", max_length=200)
    status: str | None = Field(default=None, max_length=20)  # collections with drafts
    publish_at: str | None = Field(default=None, max_length=20)  # UTC, YYYY-MM-DDTHH:MM


class StatusBody(BaseModel):
    status: str = Field(max_length=20)
    publish_at: str | None = Field(default=None, max_length=20)


class MediaRef(BaseModel):
    value: str = Field(max_length=500)


class RepoRef(BaseModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]*[A-Za-z0-9_-][A-Za-z0-9_.-]*$",
                           max_length=200)
    installation_id: int
    default_branch: str = Field(min_length=1, max_length=255)


class ReposBody(BaseModel):
    repos: list[RepoRef] = Field(max_length=50)


class NewEntryBody(BaseModel):
    values: dict
    slug: str | None = Field(default=None, max_length=80)  # address wanted, otherwise from the title
    note: str = Field(default="", max_length=200)
    status: str | None = Field(default=None, max_length=20)
    publish_at: str | None = Field(default=None, max_length=20)


def register(app: FastAPI, *, db: Database, any_session: Callable, current_session: Callable,
             manager: CmsManager, stats: SiteStats | None = None) -> None:

    def call(fn, *args):
        try:
            return fn(*args)
        except CmsConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except CmsError as exc:
            raise HTTPException(400, str(exc)) from None

    def editor(user_id: int):
        user = db.get_user_by_id(user_id)
        if user is None or user["role"] != "editor":
            raise HTTPException(404, "unknown editor")
        return user

    def user_payload(u) -> dict:
        return {"id": u["id"], "username": u["username"], "role": u["role"], "totp_enabled": bool(u["totp_secret"]),
                "created_at": u["created_at"], "apps": db.user_apps(u["id"]) if u["role"] == "editor" else []}

    def new_password() -> str:
        return secrets.token_urlsafe(12)

    # -- people (administrators) -------------------------------------------

    @app.get("/api/users")
    def list_users(_=Depends(current_session)) -> list[dict]:
        return [user_payload(u) for u in db.list_users()]

    @app.post("/api/users")
    def create_editor(body: NewUser, s=Depends(current_session)) -> dict:
        username = body.username.strip().lower()
        if not auth.USERNAME_RE.fullmatch(username):
            raise HTTPException(400, "username: 2-32 chars, lowercase letters, digits, . _ -")
        if db.get_user(username):
            raise HTTPException(409, f"{username} already exists")
        password = new_password()
        user_id = db.create_user(username, auth.hash_password(password), role="editor")
        db.audit(s.username, "editor-create", True, detail=username)
        return {**user_payload(db.get_user_by_id(user_id)), "password": password}

    @app.post("/api/users/{user_id}/password")
    def reset_password(user_id: int, s=Depends(current_session)) -> dict:
        user = editor(user_id)
        password = new_password()
        db.update_user(user_id, password_hash=auth.hash_password(password))
        db.delete_user_sessions(user_id)
        db.audit(s.username, "editor-password-reset", True, detail=user["username"])
        return {"ok": True, "password": password}

    @app.post("/api/users/{user_id}/totp/reset")
    def reset_totp(user_id: int, s=Depends(current_session)) -> dict:
        user = editor(user_id)
        db.update_user(user_id, totp_secret=None, totp_pending=None, totp_last_step=0)
        db.audit(s.username, "editor-2fa-reset", True, detail=user["username"])
        return {"ok": True}

    @app.delete("/api/users/{user_id}")
    def delete_editor(user_id: int, s=Depends(current_session)) -> dict:
        user = editor(user_id)  # administrators are managed with `mas user`
        db.delete_user_by_id(user_id)
        db.audit(s.username, "editor-delete", True, detail=user["username"])
        return {"ok": True}

    @app.put("/api/users/{user_id}/apps")
    def set_user_apps(user_id: int, body: AppIds, s=Depends(current_session)) -> dict:
        user = editor(user_id)
        unknown = [a for a in body.apps if db.get_app(a) is None]
        if unknown:
            raise HTTPException(400, f"unknown apps: {', '.join(unknown)}")
        db.set_user_apps(user_id, body.apps)
        db.audit(s.username, "editor-access", True, detail=f"{user['username']}: {', '.join(body.apps) or 'none'}")
        return user_payload(db.get_user_by_id(user_id))

    @app.get("/api/invites")
    def list_invites(_=Depends(current_session)) -> list[dict]:
        return [{k: i[k] for k in ("id", "apps", "note", "created_by", "created_at", "expires_at")}
                for i in db.pending_invites()]

    @app.post("/api/invites")
    def create_invite(body: InviteBody, s=Depends(current_session)) -> dict:
        unknown = [a for a in body.apps if db.get_app(a) is None]
        if unknown:
            raise HTTPException(400, f"unknown apps: {', '.join(unknown)}")
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + body.days * 86400
        invite_id = db.create_invite(auth.hash_token(token), body.apps, body.note.strip() or None, s.username,
                                     expires_at)
        db.audit(s.username, "invite-create", True,
                 detail=f"#{invite_id} {body.note.strip()} → {', '.join(body.apps) or 'no site'}".strip())
        # URL fragments stay in the browser and are not sent in HTTP requests,
        # access logs, or Referer headers.
        return {"id": invite_id, "path": f"/signup/#invite={token}", "expires_at": expires_at}

    @app.delete("/api/invites/{invite_id}")
    def revoke_invite(invite_id: int, s=Depends(current_session)) -> dict:
        if not db.delete_invite(invite_id):
            raise HTTPException(404, "unknown or already used invitation")
        db.audit(s.username, "invite-revoke", True, detail=f"#{invite_id}")
        return {"ok": True}

    @app.get("/api/apps/{app_id}/cms")
    async def app_cms(app_id: str, _=Depends(current_session)) -> dict:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")
        check = await asyncio.to_thread(manager.check, app_id)
        allowed = set(db.app_editors(app_id))
        editors = [{"id": u["id"], "username": u["username"], "allowed": u["id"] in allowed}
                   for u in db.list_users() if u["role"] == "editor"]
        return {**check, "editors": editors}

    @app.put("/api/apps/{app_id}/cms/editors")
    def set_app_editors(app_id: str, body: UserIds, s=Depends(current_session)) -> dict:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")
        for uid in body.user_ids:
            editor(uid)
        db.set_app_editors(app_id, body.user_ids)
        names = [db.get_user_by_id(u)["username"] for u in body.user_ids]
        db.audit(s.username, "editor-access", True, service_id=app_id, detail=", ".join(names) or "none")
        return {"ok": True}

    @app.get("/api/apps/{app_id}/cms/candidates")
    async def schema_candidates(app_id: str, _=Depends(current_session)) -> dict:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")
        return await asyncio.to_thread(call, manager.candidates, app_id)

    @app.post("/api/apps/{app_id}/cms/generate")
    async def schema_generate(app_id: str, body: Paths, _=Depends(current_session)) -> dict:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")
        return await asyncio.to_thread(call, manager.generate, app_id, body.files)

    @app.put("/api/apps/{app_id}/cms/schema")
    async def schema_write(app_id: str, body: SchemaBody, s=Depends(current_session)) -> dict:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")
        result = await asyncio.to_thread(call, manager.write_schema, app_id, body.schema_, s.username)
        db.audit(s.username, "cms-schema", True, service_id=app_id, detail=result["commit"][:7])
        return result

    @app.post("/api/cms/compatibility")
    async def compatibility(body: ReposBody, _=Depends(current_session)) -> dict:
        """Repositories with a valid mas.cms.json (badge in the repository list)."""
        return await asyncio.to_thread(call, manager.compatibility, [r.model_dump() for r in body.repos])

    @app.post("/api/apps/{app_id}/cms/collections/{cid}/table")
    async def create_table(app_id: str, cid: str, s=Depends(current_session)) -> dict:
        if db.get_app(app_id) is None:
            raise HTTPException(404, f"unknown app {app_id}")
        result = await asyncio.to_thread(call, manager.create_table, app_id, cid)
        db.audit(s.username, "cms-table", True, service_id=app_id, detail=cid)
        return result

    # -- editing (editors and administrators) ------------------------------

    def site(s, app_id: str) -> dict:
        target = db.get_app(app_id)
        if target is None or not db.can_edit(s.user_id, s.role, app_id):
            raise HTTPException(404, "unknown site")  # same answer: no hint that the app exists
        return target

    def site_payload(a: dict) -> dict:
        last = (db.list_releases(a["id"], 1) or [None])[0]
        return {"id": a["id"], "name": a["name"], "domain": a["domain"],
                "url": f"https://{a['domain']}" if a["domain"] else None,
                "last_release": {k: last[k] for k in ("id", "status", "trigger", "message", "created_at",
                                                      "finished_at")} if last else None}

    @app.get("/api/cms/sites")
    def sites(s=Depends(any_session)) -> list[dict]:
        apps = db.list_apps() if s.role == "admin" else [a for a in (db.get_app(i) for i in db.user_apps(s.user_id)) if a]
        return [site_payload(a) for a in apps if a["repo"] and a["installation_id"]]

    @app.get("/api/cms/sites/{app_id}")
    async def load(app_id: str, s=Depends(any_session)) -> dict:
        target = site(s, app_id)
        content = await asyncio.to_thread(call, manager.load, app_id)
        return {**site_payload(target), **content}

    @app.get("/api/cms/sites/{app_id}/stats")
    async def site_stats(app_id: str, days: int = Query(30, ge=7, le=365), s=Depends(any_session)) -> dict:
        """Visits per day, from the nginx access logs of the site (totals only)."""
        target = site(s, app_id)
        if stats is None or not target["domain"]:
            return {"days": [], "totals": None, "top_pages": [], "since": None, "tracked_since": None}
        return await asyncio.to_thread(stats.report, target, days)

    @app.get("/api/cms/sites/{app_id}/status")
    def status(app_id: str, s=Depends(any_session)) -> dict:
        return site_payload(site(s, app_id))

    @app.put("/api/cms/sites/{app_id}/pages/{page_id}")
    async def save(app_id: str, page_id: str, body: SaveBody, s=Depends(any_session)) -> dict:
        site(s, app_id)
        try:
            result = await asyncio.to_thread(call, manager.save, app_id, page_id, body.values, body.sha,
                                             s.user_id, s.username, body.note)
        except HTTPException as exc:
            db.audit(s.username, "cms-save", False, service_id=app_id, detail=f"{page_id}: {exc.detail}"[:500])
            raise
        if not result["unchanged"]:
            db.audit(s.username, "cms-save", True, service_id=app_id,
                     detail=f"{page_id} → {result['commit'][:7]}")
        return result

    async def save_entry(s, app_id: str, cid: str, slug: str | None, values: dict, sha: str | None, note: str,
                         new_slug: str | None = None, status: str | None = None,
                         publish_at: str | None = None) -> dict:
        site(s, app_id)
        what = f"{cid}/{slug or new_slug or 'new'}"
        try:
            result = await asyncio.to_thread(call, manager.save_entry, app_id, cid, slug, values, sha, s.user_id,
                                             s.username, note, new_slug, status, publish_at)
        except HTTPException as exc:
            db.audit(s.username, "cms-save", False, service_id=app_id, detail=f"{what}: {exc.detail}"[:500])
            raise
        if not result["unchanged"]:
            db.audit(s.username, "cms-save", True, service_id=app_id,
                     detail=f"{cid}/{result['slug']} → {(result['commit'] or 'supabase')[:8]}")
        return result

    @app.get("/api/cms/sites/{app_id}/collections/{cid}/entries/{slug}")
    async def load_entry(app_id: str, cid: str, slug: str, s=Depends(any_session)) -> dict:
        site(s, app_id)
        return await asyncio.to_thread(call, manager.load_entry, app_id, cid, slug)

    @app.post("/api/cms/sites/{app_id}/collections/{cid}/entries")
    async def create_entry(app_id: str, cid: str, body: NewEntryBody, s=Depends(any_session)) -> dict:
        return await save_entry(s, app_id, cid, None, body.values, None, body.note, (body.slug or "").strip() or None,
                                 body.status, body.publish_at)

    @app.put("/api/cms/sites/{app_id}/collections/{cid}/entries/{slug}")
    async def update_entry(app_id: str, cid: str, slug: str, body: SaveBody, s=Depends(any_session)) -> dict:
        return await save_entry(s, app_id, cid, slug, body.values, body.sha, body.note, None, body.status,
                                body.publish_at)

    @app.post("/api/cms/sites/{app_id}/collections/{cid}/entries/{slug}/status")
    async def entry_status(app_id: str, cid: str, slug: str, body: StatusBody, s=Depends(any_session)) -> dict:
        """Publish, unpublish or schedule an entry from the list, without opening it."""
        site(s, app_id)
        try:
            result = await asyncio.to_thread(call, manager.set_status, app_id, cid, slug, body.status,
                                             body.publish_at, s.username)
        except HTTPException as exc:
            db.audit(s.username, "cms-status", False, service_id=app_id, detail=f"{cid}/{slug}: {exc.detail}"[:500])
            raise
        db.audit(s.username, "cms-status", True, service_id=app_id,
                 detail=f"{cid}/{slug}: {body.status} → {(result['commit'] or '')[:8]}")
        return result

    # -- back-office: dashboard, activity, media ----------------------------------

    def visible_apps(s) -> list[dict]:
        apps = db.list_apps() if s.role == "admin" else [a for a in (db.get_app(i) for i in db.user_apps(s.user_id)) if a]
        return [a for a in apps if a["repo"] and a["installation_id"]]

    @app.get("/api/cms/dashboard")
    async def dashboard(s=Depends(any_session)) -> dict:
        """One card per site: visits of the last 7 days, last deployment, scheduled entries."""
        apps = visible_apps(s)
        scheduled = db.cms_scheduled([a["id"] for a in apps])

        def one(a: dict) -> dict:
            report = stats.report(a, 7) if stats is not None and a["domain"] else None
            return {**site_payload(a), "visits": report, "scheduled": [r for r in scheduled if r["app_id"] == a["id"]]}
        sites_ = await asyncio.gather(*(asyncio.to_thread(one, a) for a in apps))
        names = {a["id"]: a["name"] for a in apps}
        activity = [dict(r) | {"site": names.get(r["service_id"])} for r in db.cms_activity(list(names), 12)]
        totals = {k: sum((x["visits"] or {}).get("totals", {}).get(k, 0) for x in sites_ if x["visits"])
                  for k in ("pageviews", "visitors", "requests")}
        daily: dict[str, dict] = {}
        for x in sites_:
            for d in (x["visits"] or {}).get("days", []):
                row = daily.setdefault(d["day"], {"day": d["day"], "pageviews": 0, "visitors": 0})
                row["pageviews"] += d["pageviews"]
                row["visitors"] += d["visitors"]
        return {"sites": sites_, "totals": totals, "days": sorted(daily.values(), key=lambda r: r["day"]),
                "scheduled": len(scheduled), "activity": activity,
                "editors": len([u for u in db.list_users() if u["role"] == "editor"]) if s.role == "admin" else None}

    @app.get("/api/cms/activity")
    def activity(app_id: str | None = Query(None, max_length=100), limit: int = Query(100, ge=1, le=500),
                 s=Depends(any_session)) -> list[dict]:
        apps = visible_apps(s)
        ids = [a["id"] for a in apps if app_id in (None, a["id"])]
        names = {a["id"]: a["name"] for a in apps}
        rows = db.cms_activity(ids, limit)
        if s.role == "admin" and app_id is None:  # accounts and access changes have no site
            rows = sorted(rows + [r for r in db.cms_activity(None, limit) if not r["service_id"]],
                          key=lambda r: -r["id"])[:limit]
        return [{"id": r["id"], "at": r["at"], "actor": r["actor"], "action": r["action"], "ok": r["ok"],
                 "detail": r["detail"], "site": names.get(r["service_id"]), "app_id": r["service_id"]} for r in rows]

    @app.get("/api/cms/sites/{app_id}/overview")
    async def overview(app_id: str, s=Depends(any_session)) -> dict:
        site(s, app_id)
        return await asyncio.to_thread(call, manager.overview, app_id)

    @app.get("/api/cms/sites/{app_id}/media")
    async def media(app_id: str, s=Depends(any_session)) -> dict:
        site(s, app_id)
        return await asyncio.to_thread(call, manager.media, app_id)

    @app.post("/api/cms/sites/{app_id}/media")
    async def media_add(app_id: str, request: Request, folder: str = Query(max_length=300),
                        name: str = Query("image", max_length=200), s=Depends(any_session)) -> dict:
        site(s, app_id)
        limit = MAX_UPLOAD_KB * 1024
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise HTTPException(413, f"image larger than {MAX_UPLOAD_KB // 1024} MB")
        result = await asyncio.to_thread(call, manager.add_media, app_id, folder, name, bytes(data), s.username)
        db.audit(s.username, "cms-media", True, service_id=app_id, detail=f"add {result['name']}")
        return result

    @app.post("/api/cms/sites/{app_id}/media/delete")
    async def media_delete(app_id: str, body: MediaRef, s=Depends(any_session)) -> dict:
        site(s, app_id)
        result = await asyncio.to_thread(call, manager.delete_media, app_id, body.value, s.username)
        db.audit(s.username, "cms-media", True, service_id=app_id, detail=f"delete {body.value.rsplit('/', 1)[-1]}")
        return result

    @app.delete("/api/cms/sites/{app_id}/collections/{cid}/entries/{slug}")
    async def delete_entry(app_id: str, cid: str, slug: str, sha: str = Query(max_length=64),
                           s=Depends(any_session)) -> dict:
        site(s, app_id)
        try:
            result = await asyncio.to_thread(call, manager.delete_entry, app_id, cid, slug, sha, s.username)
        except HTTPException as exc:
            db.audit(s.username, "cms-delete", False, service_id=app_id, detail=f"{cid}/{slug}: {exc.detail}"[:500])
            raise
        db.audit(s.username, "cms-delete", True, service_id=app_id,
                 detail=f"{cid}/{slug} → {(result['commit'] or 'supabase')[:8]}")
        return result

    @app.post("/api/cms/sites/{app_id}/uploads")
    async def upload(app_id: str, request: Request, name: str = Query("image", max_length=200),
                     s=Depends(any_session)) -> dict:
        site(s, app_id)
        limit = MAX_UPLOAD_KB * 1024
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, f"image larger than {MAX_UPLOAD_KB // 1024} MB")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise HTTPException(413, f"image larger than {MAX_UPLOAD_KB // 1024} MB")
        token = await asyncio.to_thread(call, manager.stage_upload, app_id, s.user_id, name, bytes(data))
        return {"value": f"mas-upload:{token}"}

    @app.get("/api/cms/sites/{app_id}/image")
    async def image(app_id: str, src: str = Query(max_length=500), s=Depends(any_session)) -> Response:
        site(s, app_id)
        data, mime = await asyncio.to_thread(call, manager.read_image, app_id, src)
        return Response(data, media_type=mime, headers={"Content-Security-Policy": "default-src 'none'"})
