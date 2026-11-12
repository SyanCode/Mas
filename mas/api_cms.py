"""API routes of the CMS: editor accounts, per-app access, and the editing endpoints."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mas import auth, cookies as cookies_mod
from mas.cms import MAX_UPLOAD_KB, CmsConflict, CmsError, CmsManager
from mas.db import Database
from mas.geo import ATTRIBUTION, GeoError
from mas.notify import EVENTS, NotifyError
from mas.sitestats import SiteStats


# What an automatic audit cannot see: kept as a short checklist for a human (ids are translated by the UI).
MANUAL_CHECKS = ["register", "processors", "retention", "rights", "breach", "dpo"]


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
             manager: CmsManager, stats: SiteStats | None = None, geo=None, compliance=None, notifier=None, uptime=None) -> None:

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
            return {"days": [], "totals": None, "top_pages": [], "countries": [], "geo": False, "since": None,
                    "tracked_since": None}
        report = await asyncio.to_thread(stats.report, target, days)
        return {**report, "attribution": ATTRIBUTION}

    @app.put("/api/cms/sites/{app_id}/goals")
    def goals_config(app_id: str, body: dict, s=Depends(any_session)) -> dict:
        """Pages that count as a goal ("/merci", "/blog/*") and the steps of the funnel."""
        site(s, app_id)

        def clean(items, pattern) -> list[str]:
            items = items if isinstance(items, list) else []
            if len(items) > 20 or any(not isinstance(i, str) or not pattern.fullmatch(i) for i in items):
                raise HTTPException(400, "invalid goals")
            return list(dict.fromkeys(items))
        paths = clean(body.get("paths"), re.compile(r"^/[A-Za-z0-9._~%/\-]{0,100}\*?$"))
        funnel = clean(body.get("funnel"), re.compile(r"^(page:/[A-Za-z0-9._~%/\-]{0,100}\*?|[a-z0-9_.:-]{1,40})$"))
        db.set_setting(f"goals:{app_id}", json.dumps({"paths": paths, "funnel": funnel}))
        db.audit(s.username, "cms-goals", True, service_id=app_id, detail=f"{len(paths)} page(s), {len(funnel)} step(s)")
        return {"paths": paths, "funnel": funnel}

    @app.post("/api/cms/geo/install")
    async def geo_install(s=Depends(current_session)) -> dict:
        """Download the country database (visitors map). Administrators only."""
        if geo is None:
            raise HTTPException(404, "countries are not available")
        try:
            result = await asyncio.to_thread(geo.install)
        except GeoError as exc:
            db.audit(s.username, "cms-geo", False, detail=str(exc)[:500])
            raise HTTPException(502, str(exc)) from None
        db.audit(s.username, "cms-geo", True, detail=result["month"])
        return result

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
            last = compliance.latest(a["id"]) if compliance is not None else None
            return {**site_payload(a), "visits": report, "scheduled": [r for r in scheduled if r["app_id"] == a["id"]],
                    "compliance": {"score": last["score"], "verdict": last["verdict"], "at": last["at"]} if last else None}
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
        countries: dict[str, int] = {}
        for x in sites_:
            for c in (x["visits"] or {}).get("countries", []):
                countries[c["code"]] = countries.get(c["code"], 0) + c["visitors"]
        return {"sites": sites_, "totals": totals,
                "countries": [{"code": k, "visitors": v} for k, v in sorted(countries.items(), key=lambda kv: -kv[1])], "days": sorted(daily.values(), key=lambda r: r["day"]),
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

    @app.get("/api/cms/sites/{app_id}/cookies")
    def cookies_get(app_id: str, s=Depends(any_session)) -> dict:
        site(s, app_id)
        return {"config": cookies_mod.load(db, app_id), "stats": cookies_mod.report(db, app_id),
                "log": [{"at": r["at"], "consent_id": r["consent_id"], "decision": r["decision"],
                         "categories": json.loads(r["categories"] or "{}"), "banner": r["banner"]}
                        for r in db.consent_log(app_id, 20)]}

    @app.get("/api/cms/sites/{app_id}/cookies/log.csv")
    def cookies_log_csv(app_id: str, s=Depends(any_session)) -> Response:
        """The proof of consent, as a file to keep or hand to a regulator."""
        site(s, app_id)
        db.audit(s.username, "cms-consent-export", True, service_id=app_id)
        return Response(cookies_mod.log_csv(db, app_id), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="consent-log-{app_id}.csv"'})

    @app.get("/api/cms/sites/{app_id}/cookies/versions")
    def cookies_versions(app_id: str, s=Depends(any_session)) -> list[dict]:
        site(s, app_id)
        return [{"version": v["version"], "first_seen": v["first_seen"], "config": json.loads(v["config"])}
                for v in db.consent_versions(app_id)]

    @app.put("/api/cms/sites/{app_id}/cookies")
    def cookies_put(app_id: str, body: dict, s=Depends(any_session)) -> dict:
        site(s, app_id)
        try:
            config = cookies_mod.save(db, app_id, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "cms-cookies", True, service_id=app_id, detail="on" if config["enabled"] else "off")
        return {"config": config, "stats": cookies_mod.report(db, app_id)}

    @app.get("/api/cms/sites/{app_id}/compliance")
    def compliance_get(app_id: str, s=Depends(any_session)) -> dict:
        target = site(s, app_id)
        if compliance is None:
            raise HTTPException(404, "audits are not available")
        return {"latest": compliance.latest(app_id), "history": compliance.history(app_id),
                "domain": target["domain"], "manual": MANUAL_CHECKS}

    @app.post("/api/cms/sites/{app_id}/compliance/run")
    async def compliance_run(app_id: str, s=Depends(any_session)) -> dict:
        target = site(s, app_id)
        if compliance is None or not target["domain"]:
            raise HTTPException(400, "the site needs a domain to be audited")
        last = compliance.latest(app_id)
        if last and time.time() - datetime.fromisoformat(last["at"]).timestamp() < 20:
            return {"latest": last, "history": compliance.history(app_id), "domain": target["domain"],
                    "manual": MANUAL_CHECKS}
        try:
            await asyncio.to_thread(compliance.run, target, None, "manual")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"latest": compliance.latest(app_id), "history": compliance.history(app_id),
                "domain": target["domain"], "manual": MANUAL_CHECKS}

    @app.get("/api/cms/sites/{app_id}/changes")
    def changes(app_id: str, s=Depends(any_session)) -> list[dict]:
        site(s, app_id)
        return manager.changes(app_id)

    @app.post("/api/cms/sites/{app_id}/changes/{change_id}/revert")
    async def revert(app_id: str, change_id: int, s=Depends(any_session)) -> dict:
        """Undo a change with a new commit (the history keeps both)."""
        site(s, app_id)
        try:
            result = await asyncio.to_thread(call, manager.revert, app_id, change_id, s.username)
        except HTTPException as exc:
            db.audit(s.username, "cms-revert", False, service_id=app_id, detail=f"#{change_id}: {exc.detail}"[:500])
            raise
        db.audit(s.username, "cms-revert", True, service_id=app_id, detail=f"#{change_id} → {result['commit'][:8]}")
        return result

    @app.get("/api/cms/sites/{app_id}/uptime")
    async def uptime_get(app_id: str, days: int = Query(30, ge=1, le=35), s=Depends(any_session)) -> dict:
        site(s, app_id)
        return await asyncio.to_thread(uptime.report, app_id, days)

    @app.put("/api/cms/sites/{app_id}/uptime")
    def uptime_put(app_id: str, body: dict, s=Depends(current_session)) -> dict:
        """Switch the public status page (/status/<site>) on or off."""
        site(s, app_id)
        on = bool(body.get("status_page"))
        db.set_setting(f"status_page:{app_id}", "1" if on else None)
        db.audit(s.username, "cms-status-page", True, service_id=app_id, detail="on" if on else "off")
        return {"status_page": on}

    # -- notifications (administrators) -------------------------------------------------------------

    @app.get("/api/notify")
    def notify_get(_=Depends(current_session)) -> dict:
        return {"smtp": notifier.smtp(), "events": list(EVENTS)}

    @app.put("/api/notify/smtp")
    def notify_smtp(body: dict, s=Depends(current_session)) -> dict:
        try:
            result = notifier.set_smtp(body)
        except NotifyError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "cms-notify-config", True, detail="mail server")
        return result

    @app.get("/api/cms/sites/{app_id}/notify")
    def site_notify_get(app_id: str, s=Depends(current_session)) -> dict:
        site(s, app_id)
        return {**notifier.site(app_id), "events": notifier.site(app_id)["events"], "all_events": list(EVENTS)}

    @app.put("/api/cms/sites/{app_id}/notify")
    def site_notify_put(app_id: str, body: dict, s=Depends(current_session)) -> dict:
        site(s, app_id)
        try:
            result = notifier.set_site(app_id, body)
        except NotifyError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "cms-notify-config", True, service_id=app_id, detail=", ".join(result["events"]))
        return {**result, "all_events": list(EVENTS)}

    @app.post("/api/cms/sites/{app_id}/notify/test")
    async def site_notify_test(app_id: str, s=Depends(current_session)) -> dict:
        target = site(s, app_id)
        cfg = notifier.site(app_id, reveal=True)
        if not cfg["emails"] and not cfg["webhook"]:
            raise HTTPException(400, "add an e-mail address or a webhook first")
        errors = []
        if cfg["webhook"]:
            try:
                await asyncio.to_thread(notifier.send_webhook, cfg["webhook"], f"{target['name']}: test", "Mas can reach this channel.", "test", app_id)
            except NotifyError as exc:
                errors.append(str(exc))
        if cfg["emails"]:
            try:
                await asyncio.to_thread(notifier.send_email, cfg["emails"], f"[{target['name']}] test", "Mas can send you e-mails.")
            except NotifyError as exc:
                errors.append(str(exc))
        if errors:
            raise HTTPException(502, "; ".join(errors))
        return {"ok": True}

    @app.post("/api/cms/sites/{app_id}/notify/digest")
    async def site_notify_digest(app_id: str, s=Depends(current_session)) -> dict:
        """Send the weekly report now (to check how it looks)."""
        target = site(s, app_id)
        text = await asyncio.to_thread(notifier.digest_text, target)
        cfg = notifier.site(app_id, reveal=True)
        if cfg["emails"]:
            try:
                await asyncio.to_thread(notifier.send_email, cfg["emails"], f"[{target['name']}] Weekly report", text)
            except NotifyError as exc:
                raise HTTPException(502, str(exc)) from None
        return {"text": text, "sent": bool(cfg["emails"])}

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
