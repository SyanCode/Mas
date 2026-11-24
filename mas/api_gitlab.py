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


class MirrorBody(BaseModel):
    project: str | None = Field(default=None, max_length=300)  # group/name on the GitLab; empty: stop mirroring


def register(app: FastAPI, *, host: Host, config: Config, db: Database, current_session: Callable,
             require_password: Callable, deployer, scanner, cluster, mirror: gl_mod.Mirror) -> Callable:
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
        return out

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

    @app.post("/api/apps/{app_id}/mirror/sync")
    async def sync_mirror(app_id: str, _=Depends(current_session)) -> dict:
        target = find(app_id)
        if not mirror.get(app_id):
            raise HTTPException(409, "this app is not mirrored")
        ok = await asyncio.to_thread(mirror.sync, target)
        return {"ok": ok, "mirror": mirror.get(app_id)}

    return gitlab
