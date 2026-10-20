"""API routes of the CMS: editor accounts, per-app access, and the editing endpoints."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mas import auth
from mas.cms import MAX_UPLOAD_KB, CmsConflict, CmsError, CmsManager
from mas.db import Database


class NewUser(BaseModel):
    username: str = Field(max_length=32)


class AppIds(BaseModel):
    apps: list[str] = Field(max_length=500)


class UserIds(BaseModel):
    user_ids: list[int] = Field(max_length=500)


class SaveBody(BaseModel):
    values: dict
    sha: str | None = Field(default=None, max_length=64)  # blob sha the editor started from
    note: str = Field(default="", max_length=200)


def register(app: FastAPI, *, db: Database, any_session: Callable, current_session: Callable,
             manager: CmsManager) -> None:

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
