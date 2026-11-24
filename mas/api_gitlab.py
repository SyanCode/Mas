"""API routes of GitLab: connect one (or find the one of this server or of the cluster), its projects, its webhook,
and the mirror of an app's GitHub repository into it. See mas.gitlab."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from mas import gitlab as gl_mod
from mas.config import Config
from mas.db import Database
from mas.envcrypt import load_fernet
from mas.host import Host

MAX_WEBHOOK_BYTES = 5 * 1024 * 1024


class ConnectBody(BaseModel):
    url: str = Field(max_length=300)
    token: str = Field(max_length=300)
    password: str = Field(default="", max_length=200)


class BackupBody(BaseModel):
    enabled: bool


class MirrorBody(BaseModel):
    project: str | None = Field(default=None, max_length=300)  # group/name on the GitLab; empty: stop mirroring


def register(app: FastAPI, *, host: Host, config: Config, db: Database, current_session: Callable,
             require_password: Callable, deployer, scanner, cluster, mirror: gl_mod.Mirror,
             backup: gl_mod.OmnibusBackup | None = None) -> Callable:
    """Returns the ``gitlab()`` factory (None while no GitLab is connected)."""
    cache: dict = {}
    seen: dict[str, float] = {}

    def gitlab() -> gl_mod.GitLab | None:
        try:
            cfg = gl_mod.load(db, load_fernet(host, config.secret_key_path))
        except Exception:  # noqa: BLE001 - no secret key yet
            return None
        if cfg is None:
            return None
        key = (cfg["url"], hash(cfg["token"]))
        if cache.get("key") != key:
            cache.update(key=key, client=gl_mod.GitLab(cfg["url"], cfg["token"]))
        return cache["client"]

    def secret() -> str | None:
        try:
            cfg = gl_mod.load(db, load_fernet(host, config.secret_key_path))
        except Exception:  # noqa: BLE001
            return None
        return cfg["secret"] if cfg else None

    def base(request: Request) -> str:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        return f"{proto}://{request.headers.get('host', '')}"

    def require() -> gl_mod.GitLab:
        client = gitlab()
        if client is None:
            raise HTTPException(409, "connect a GitLab first (Settings → GitLab)")
        return client

    async def call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except gl_mod.GitLabError as exc:
            raise HTTPException(502, str(exc)) from None

    def detected() -> dict:
        local = gl_mod.detect(host, scanner.result().services)
        remote = []
        for server in db.list_cluster_servers():
            try:
                info = cluster._call(server, "GET", "/api/cluster/self", timeout=6)
            except Exception:  # noqa: BLE001 - an unreachable server simply shows nothing
                continue
            for g in (info or {}).get("gitlab") or []:
                remote.append({**g, "server": server.get("name") or server.get("address")})
        return {"local": local, "cluster": remote}

    # -- connection ------------------------------------------------------------------------------------

    @app.get("/api/gitlab")
    async def status(request: Request, _=Depends(current_session)) -> dict:
        client = gitlab()
        out = {"connected": client is not None, "url": client.url if client else None,
               "webhook_url": f"{base(request)}/api/gitlab/webhook", "user": None, "error": None}
        if client is not None:
            try:
                out["user"] = await asyncio.to_thread(client.whoami)
            except gl_mod.GitLabError as exc:
                out["error"] = str(exc)
        out["detected"] = await asyncio.to_thread(detected)
        if backup is not None and backup.available():
            out["backup"] = {"enabled": backup.enabled(), "archives": backup.list(), **jobs.get("backup", {})}
        return out

    jobs: dict[str, dict] = {}

    @app.put("/api/gitlab/backup")
    def set_backup(body: BackupBody, s=Depends(current_session)) -> dict:
        if backup is None or not backup.available():
            raise HTTPException(404, "no GitLab package on this server")
        backup.set_enabled(body.enabled)
        db.audit(s.username, "gitlab-backup-set", True, detail="on" if body.enabled else "off")
        return {"enabled": body.enabled}

    @app.post("/api/gitlab/backup/run")
    def run_backup(s=Depends(current_session)) -> dict:
        if backup is None or not backup.available():
            raise HTTPException(404, "no GitLab package on this server")
        if jobs.get("backup", {}).get("running"):
            raise HTTPException(409, "a backup of the GitLab is already running")
        job = {"running": True, "error": None, "result": None}
        jobs["backup"] = job

        def work() -> None:
            try:
                job["result"] = backup.run().name
                db.audit(s.username, "gitlab-backup", True, detail=job["result"])
            except gl_mod.GitLabError as exc:
                job["error"] = str(exc)
                db.audit(s.username, "gitlab-backup", False, detail=str(exc)[:300])
            finally:
                job["running"] = False
        import threading
        threading.Thread(target=work, daemon=True, name="mas-gitlab-backup").start()
        return job

    @app.put("/api/gitlab")
    async def connect(body: ConnectBody, s=Depends(current_session)) -> dict:
        require_password(s, body.password)  # a secret goes in
        try:
            client = gl_mod.GitLab(body.url, body.token.strip())
            user = await asyncio.to_thread(client.whoami)
            gl_mod.save(db, load_fernet(host, config.secret_key_path), client.url, body.token.strip())
        except gl_mod.GitLabError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "gitlab-connect", True, detail=f"{client.url} as {user.get('username')}")
        return {"connected": True, "url": client.url, "user": user}

    @app.delete("/api/gitlab")
    def disconnect(s=Depends(current_session)) -> dict:
        gl_mod.forget(db)
        cache.clear()
        db.audit(s.username, "gitlab-disconnect", True)
        return {"connected": False}

    @app.get("/api/gitlab/repos")
    async def repos(_=Depends(current_session)) -> list[dict]:
        return await call(require().repos)

    @app.get("/api/gitlab/branches")
    async def branches(repo: str, _=Depends(current_session)) -> list[str]:
        return await call(require().branches, None, repo)

    @app.get("/api/gitlab/detect")
    async def detect_type(repo: str, ref: str, _=Depends(current_session)) -> dict:
        client = require()
        found = await call(client.detect_type, None, repo, ref)
        return {"type": found.value}

    # -- webhook (GitLab → Mas) --------------------------------------------------------------------------

    @app.post("/api/gitlab/webhook")
    async def webhook(request: Request) -> dict:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "webhook payload too large")
        if not gl_mod.verify_token(secret(), request.headers.get("x-gitlab-token")):
            raise HTTPException(401, "invalid token")
        delivery = request.headers.get("x-gitlab-event-uuid", "")  # a captured delivery must not be replayable
        if delivery:
            key = f"{delivery}:{request.headers.get('x-gitlab-event', '')}"
            if key in seen:
                raise HTTPException(409, "delivery already processed")
            seen[key] = time.time()
            if len(seen) > 2000:
                for k in sorted(seen, key=seen.get)[:1000]:
                    seen.pop(k, None)
        try:
            payload = json.loads(bytes(body))
        except ValueError:
            raise HTTPException(400, "invalid JSON") from None
        event = request.headers.get("x-gitlab-event", "")
        if event == "Push Hook":
            push = gl_mod.push_event(payload)
            if push is None:
                return {"ok": True, "deployed": []}
            return await asyncio.to_thread(request.app.state.handle_push, "gitlab", push["repo"], push["branch"],
                                           push["sha"], push["actor"], push["head_commit"], push["commits"], payload)
        if event == "Merge Request Hook":
            pr = gl_mod.merge_request_event(payload)
            if pr is None:
                return {"ok": True, "ignored": "merge request"}
            return await asyncio.to_thread(request.app.state.previews.handle, pr,
                                           pr["pull_request"]["user"]["login"], "gitlab")
        return {"ok": True, "ignored": event or "unknown"}

    def ensure_hook(repo: str, public: str) -> dict | None:
        client, key = gitlab(), secret()
        if client is None or not key:
            return None
        try:
            added = client.ensure_hook(repo, f"{public}/api/gitlab/webhook", key)
            return {"ok": True, "added": added}
        except gl_mod.GitLabError as exc:  # no Maintainer right: the person adds it by hand
            return {"ok": False, "error": str(exc)[:300]}

    app.state.gitlab_hook = ensure_hook

    # -- mirror of an app's GitHub repository -------------------------------------------------------------

    def find(app_id: str) -> dict:
        target = db.get_app(app_id)
        if target is None:
            raise HTTPException(404, "unknown app")
        return target

    @app.get("/api/apps/{app_id}/mirror")
    def get_mirror(app_id: str, _=Depends(current_session)) -> dict:
        find(app_id)
        return {"mirror": mirror.get(app_id), "gitlab": gitlab().url if gitlab() else None}

    @app.put("/api/apps/{app_id}/mirror")
    async def put_mirror(app_id: str, body: MirrorBody, s=Depends(current_session)) -> dict:
        target = find(app_id)
        try:
            state = await asyncio.to_thread(mirror.configure, target, (body.project or "").strip() or None)
        except gl_mod.GitLabError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "gitlab-mirror-set", True, service_id=app_id, detail=(state or {}).get("project") or "off")
        if state:
            asyncio.get_running_loop().run_in_executor(None, mirror.sync, db.get_app(app_id))
        return {"mirror": state}

    @app.post("/api/apps/{app_id}/move-to-gitlab")
    async def move_to_gitlab(app_id: str, body: MirrorBody, request: Request, s=Depends(current_session)) -> dict:
        """Deploy an app from GitLab instead of GitHub, without a rebuild nor a second of downtime: the repository is
        copied (every branch and tag), Mas checks that GitLab has the very commit that is live, then the app follows
        GitLab (webhook added). The previews of GitHub pull requests are removed; the GitHub repository is not touched."""
        target = find(app_id)
        if (target.get("source") or "github") == "gitlab":
            raise HTTPException(409, "this app is already deployed from GitLab")
        if target["mode"] != "managed" or target.get("environment") == "preview":
            raise HTTPException(409, "only an app Mas builds from GitHub can move")
        client = require()
        project = (body.project or (mirror.get(app_id) or {}).get("project") or "").strip()
        if not project:
            raise HTTPException(400, "give the GitLab project (group/name)")
        try:
            if (mirror.get(app_id) or {}).get("project") != project:
                await asyncio.to_thread(mirror.configure, target, project)
            if not await asyncio.to_thread(mirror.sync, target):
                raise gl_mod.GitLabError((mirror.get(app_id) or {}).get("error") or "the copy to GitLab failed")
            project = mirror.get(app_id)["project"]
            gitlab_head = await asyncio.to_thread(client.head, None, project, target["branch"])
        except gl_mod.GitLabError as exc:
            raise HTTPException(400, str(exc)) from None
        live = db.get_release(target["current_release"]) if target.get("current_release") else None
        if live and live.get("sha") and live["sha"] != gitlab_head:
            raise HTTPException(409, f"GitLab's {target['branch']} is at {gitlab_head[:7]} but {live['sha'][:7]} is live: "
                                     "push or deploy first, then move again")
        removed = await asyncio.to_thread(request.app.state.previews.remove_all, app_id)
        db.update_app(app_id, source="gitlab", repo=project, installation_id=None)
        mirror.configure(db.get_app(app_id), None)
        hook = await asyncio.to_thread(ensure_hook, project, base(request))
        db.audit(s.username, "app-move-gitlab", True, service_id=app_id,
                 detail=f"{target['repo']} → {client.url}/{project} at {gitlab_head[:7]}")
        return {"moved": True, "repo": project, "sha": gitlab_head, "webhook": hook, "previews_removed": removed}

    @app.post("/api/apps/{app_id}/mirror/sync")
    async def sync_mirror(app_id: str, _=Depends(current_session)) -> dict:
        target = find(app_id)
        if not mirror.get(app_id):
            raise HTTPException(409, "this app is not mirrored")
        ok = await asyncio.to_thread(mirror.sync, target)
        return {"ok": ok, "mirror": mirror.get(app_id)}

    return gitlab
