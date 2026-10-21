"""API routes for phase 3 (GitHub, apps, releases) and the health checks.

Registered by ``mas.api.create_app``. The webhook is the only unauthenticated
route besides login/health: its HMAC signature is verified before anything
else is read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from mas.appdb import AppDbError
from mas.config import Config, save_section
from mas.db import Database
from mas.deploy import (
    BRANCH_RE, MAX_INSTANCES, PROXY_TYPES, REPO_RE, DeployError, Deployer, allocate_port, app_backends,
    app_instances, suggest_start_command, used_ports, validate_app, validate_backends, validate_commands,
)
from mas.dns import server_ips
from mas.envcrypt import EnvError, decrypt_env, encrypt_env, load_fernet, parse_env
from mas.github import GitHubApp, GitHubError, convert_manifest, repo_from_remote, verify_signature
from mas.health import run_checks
from mas.healthfix import ACTIONS, FixError, HealthFixer
from mas.host import Host
from mas.models import ServiceType
from mas.state import Scanner

log = logging.getLogger(__name__)

FINAL_STATUSES = {"live", "failed", "superseded"}
HEALTH_TTL = 300


class ManifestBody(BaseModel):
    org: str | None = Field(default=None, max_length=100)


class ManifestComplete(BaseModel):
    code: str = Field(max_length=200)
    state: str = Field(max_length=200)


class CreateApp(BaseModel):
    id: str = Field(max_length=24)
    repo: str = Field(max_length=200)
    branch: str = Field(max_length=200)
    installation_id: int
    domain: str | None = Field(default=None, max_length=253)
    type: ServiceType
    env: str = Field(default="", max_length=65536)
    build_command: str | None = Field(default=None, max_length=1000)
    start_command: str | None = Field(default=None, max_length=1000)
    root_dir: str = Field(default="", max_length=200)
    auto_deploy: bool = True
    supabase: bool = False  # connect it to the Supabase of this server before its first build
    supabase_seed: bool = False


class HealthFix(BaseModel):
    action: str = Field(max_length=40)
    target: str = Field(max_length=253)


class AdoptService(BaseModel):
    service_id: str = Field(max_length=200)
    id: str | None = Field(default=None, max_length=24)
    branch: str | None = Field(default=None, max_length=200)


class PatchApp(BaseModel):
    branch: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    build_command: str | None = Field(default=None, max_length=1000)
    start_command: str | None = Field(default=None, max_length=1000)
    root_dir: str | None = Field(default=None, max_length=200)
    auto_deploy: bool | None = None


class ScalingBody(BaseModel):
    instances: int = Field(ge=1, le=MAX_INSTANCES)
    backends: list[str] = Field(default_factory=list, max_length=MAX_INSTANCES)


class EnvBody(BaseModel):
    text: str = Field(max_length=65536)


class RollbackBody(BaseModel):
    release_id: int


def app_payload(app: dict, db: Database) -> dict:
    data = {k: v for k, v in app.items() if k != "env"}
    data["auto_deploy"] = bool(app["auto_deploy"])
    data["has_env"] = bool(app["env"])
    data["instances"] = len(app_instances(app)) or 1
    data["instance_ports"] = [p for _, p in app_instances(app)]
    data["backends"] = app_backends(app)
    data.pop("extra_ports", None)
    data["stack"] = json.loads(app["stack"]) if app.get("stack") else None  # template and options, no secret
    releases = db.list_releases(app["id"], 1)
    data["last_release"] = releases[0] if releases else None
    data["current"] = db.get_release(app["current_release"]) if app["current_release"] else None
    return data


def register(
    app: FastAPI,
    *,
    host: Host,
    config: Config,
    db: Database,
    scanner: Scanner,
    current_session: Callable,
    deployer: Deployer,
    github_client: Callable[[], GitHubApp | None],
    reset_github: Callable[[], None],
    appdb=None,
) -> None:
    manifest_states: dict[str, tuple[int, float]] = {}
    health_cache: dict[str, object] = {}

    def require_github() -> GitHubApp:
        gh = github_client()
        if gh is None:
            raise HTTPException(409, "GitHub App not configured (Settings → GitHub)")
        return gh

    def find_app(app_id: str) -> dict:
        found = db.get_app(app_id)
        if found is None:
            raise HTTPException(404, f"unknown app {app_id}")
        return found

    def gh_call(fn, *args):
        try:
            return fn(*args)
        except GitHubError as exc:
            raise HTTPException(502, str(exc)) from None

    # -- health (phase 2.5) ------------------------------------------------

    @app.get("/api/health/checks")
    async def health_checks(refresh: bool = False, _=Depends(current_session)) -> dict:
        cached_at = health_cache.get("at", 0.0)
        if refresh or time.time() - float(cached_at) > HEALTH_TTL:
            checks = await asyncio.to_thread(run_checks, host, config, True, server_ips(db, config)[0])
            health_cache.update(at=time.time(), checks=[
                {**c.as_dict(), "action": {"id": c.action, **ACTIONS[c.action]} if c.action else None}
                for c in checks])
        return {"checked_at": health_cache["at"], "checks": health_cache["checks"]}

    fixer = HealthFixer(host, config, Path(db.path).parent / "backups")

    @app.post("/api/health/fix")
    async def health_fix(body: HealthFix, s=Depends(current_session)) -> dict:
        """Apply the fix of a reported problem (never a command sent by the browser)."""
        try:
            result = await asyncio.to_thread(fixer.apply, body.action, body.target)
        except FixError as exc:
            db.audit(s.username, f"fix-{body.action}", False, detail=f"{body.target}: {exc}"[:500])
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, f"fix-{body.action}", result["ok"], detail=body.target)
        health_cache.clear()  # the next visit checks again
        return result

    # -- GitHub App --------------------------------------------------------

    def public_base(request: Request) -> str:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        return f"{proto}://{request.headers.get('host', '')}"

    @app.get("/api/github/status")
    async def github_status(request: Request, _=Depends(current_session)) -> dict:
        base = public_base(request)
        out = {"configured": config.github_configured, "app_id": config.github_app_id,
               "slug": config.github_app_slug, "webhook_url": f"{base}/api/github/webhook",
               "installations": [], "error": None}
        if config.github_app_slug:
            out["install_url"] = f"https://github.com/apps/{config.github_app_slug}/installations/new"
        gh = github_client()
        if gh is not None:
            try:
                insts = await asyncio.to_thread(gh.installations)
                out["installations"] = [{"id": i["id"], "account": i.get("account", {}).get("login")} for i in insts]
            except GitHubError as exc:
                out["error"] = str(exc)
        return out

    @app.post("/api/github/manifest")
    def github_manifest(body: ManifestBody, request: Request, s=Depends(current_session)) -> dict:
        base = public_base(request)
        if not base.startswith("https://"):
            raise HTTPException(400, "open the dashboard through its public HTTPS domain to create the GitHub App")
        state = secrets.token_urlsafe(24)
        now = time.time()
        for key in [k for k, (_, exp) in manifest_states.items() if exp < now]:
            manifest_states.pop(key, None)
        manifest_states[state] = (s.user_id, now + 3600)
        hostname = request.headers.get("host", "server").split(":")[0]
        manifest = {
            "name": f"Mas {hostname}"[:34],
            "url": base,
            "hook_attributes": {"url": f"{base}/api/github/webhook", "active": True},
            "redirect_url": f"{base}/settings/",
            "public": False,
            "default_permissions": {"contents": "write", "metadata": "read", "pull_requests": "read"},
            "default_events": ["push", "pull_request"],
        }
        owner = f"organizations/{body.org}/" if body.org else ""
        return {"action": f"https://github.com/{owner}settings/apps/new?state={state}",
                "manifest": json.dumps(manifest)}

    @app.post("/api/github/manifest/complete")
    async def github_manifest_complete(body: ManifestComplete, s=Depends(current_session)) -> dict:
        entry = manifest_states.pop(body.state, None)
        if entry is None or entry[0] != s.user_id or entry[1] < time.time():
            raise HTTPException(400, "invalid or expired state, start again")
        try:
            data = await asyncio.to_thread(convert_manifest, body.code)
        except GitHubError as exc:
            raise HTTPException(502, str(exc)) from None
        host.write_text(config.github_private_key_path, data["pem"], mode=0o600)
        values = {"app_id": int(data["id"]), "app_slug": data["slug"],
                  "private_key_path": config.github_private_key_path, "webhook_secret": data["webhook_secret"]}
        save_section(host, "github", values)
        config.github_app_id, config.github_app_slug = int(data["id"]), data["slug"]
        config.github_webhook_secret = data["webhook_secret"]
        reset_github()
        db.audit(s.username, "github-app-create", True, detail=f"app {data['slug']} ({data['id']})")
        return {"ok": True, "slug": data["slug"],
                "install_url": f"https://github.com/apps/{data['slug']}/installations/new"}

    @app.get("/api/github/repos")
    async def github_repos(_=Depends(current_session)) -> list[dict]:
        gh = require_github()
        return await asyncio.to_thread(gh_call, gh.repos)

    def check_repo(repo: str, ref: str | None = None) -> None:
        # Both end up in GitHub API paths: no "../", "?" or "&" smuggling.
        if not REPO_RE.fullmatch(repo) or ".." in repo:
            raise HTTPException(400, "invalid repository")
        if ref is not None and not BRANCH_RE.fullmatch(ref):
            raise HTTPException(400, "invalid branch")

    @app.get("/api/github/branches")
    async def github_branches(repo: str, installation_id: int, _=Depends(current_session)) -> list[str]:
        check_repo(repo)
        gh = require_github()
        return await asyncio.to_thread(gh_call, gh.branches, installation_id, repo)

    @app.get("/api/github/detect")
    async def github_detect(repo: str, installation_id: int, ref: str, _=Depends(current_session)) -> dict:
        check_repo(repo, ref)
        gh = require_github()
        detected = await asyncio.to_thread(gh_call, gh.detect_type, installation_id, repo, ref)
        files = {"manage.py"} if detected == ServiceType.PYTHON else set()
        return {"type": detected.value, "start_command": suggest_start_command(detected, files)}

    seen_deliveries: dict[str, float] = {}

    @app.post("/api/github/webhook")
    async def github_webhook(request: Request) -> dict:
        body = await request.body()
        if not verify_signature(config.github_webhook_secret, body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(401, "invalid signature")
        # A captured (validly signed) delivery must not be replayable.
        delivery = request.headers.get("x-github-delivery", "")
        if delivery:
            if delivery in seen_deliveries:
                raise HTTPException(409, "delivery already processed")
            seen_deliveries[delivery] = time.time()
            if len(seen_deliveries) > 2000:
                for key in sorted(seen_deliveries, key=seen_deliveries.get)[:1000]:
                    seen_deliveries.pop(key, None)
        event = request.headers.get("x-github-event", "")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "invalid JSON") from None
        if event == "ping":
            return {"ok": True, "pong": True}
        if event == "push":
            ref = payload.get("ref", "")
            sha = payload.get("after", "")
            repo = (payload.get("repository") or {}).get("full_name", "")
            if not ref.startswith("refs/heads/") or payload.get("deleted") or not sha or set(sha) == {"0"}:
                return {"ok": True, "deployed": []}
            branch = ref[len("refs/heads/"):]
            actor = (payload.get("pusher") or {}).get("name") or "github"
            deployed = []
            for target in db.apps_for_push(repo, branch):
                deployer.enqueue(target["id"], "push", f"github:{actor}", sha)
                deployed.append(target["id"])
            db.audit(f"github:{actor}", "webhook-push", True,
                     detail=f"{repo}@{branch} {sha[:7]} → {', '.join(deployed) or 'no app'}")
            return {"ok": True, "deployed": deployed}
        if event == "pull_request":
            # Preview deployments are not implemented yet; the event is acknowledged.
            return {"ok": True, "ignored": "pull_request"}
        return {"ok": True, "ignored": event}

    # -- apps --------------------------------------------------------------

    @app.get("/api/apps")
    def list_apps(_=Depends(current_session)) -> list[dict]:
        return [app_payload(a, db) for a in db.list_apps()]

    @app.post("/api/apps")
    async def create_app(body: CreateApp, s=Depends(current_session)) -> dict:
        domain = (body.domain or "").strip().lower() or None
        try:
            validate_app(body.id, body.repo, body.branch, domain, body.type.value)
            validate_commands(body.build_command, body.start_command, body.root_dir.strip("/"))
            env = parse_env(body.env)
        except (DeployError, EnvError) as exc:
            raise HTTPException(400, str(exc)) from None
        if db.get_app(body.id):
            raise HTTPException(409, f"app {body.id} already exists")
        if body.type == ServiceType.PYTHON and not (body.start_command or "").strip():
            raise HTTPException(400, "python apps need a start command")
        port = allocate_port(host, config, db) if body.type in PROXY_TYPES else None
        token = encrypt_env(load_fernet(host, config.secret_key_path), env) if env else None
        db.create_app(body.id, name=body.id, repo=body.repo, branch=body.branch,
                      installation_id=body.installation_id, domain=domain, type=body.type.value, port=port,
                      mode="managed", root_dir=body.root_dir.strip("/"),
                      build_command=(body.build_command or "").strip() or None,
                      start_command=(body.start_command or "").strip() or None,
                      env=token, auto_deploy=int(body.auto_deploy))
        db.audit(s.username, "app-create", True, detail=f"{body.id}: {body.repo}@{body.branch} → {domain}")
        if body.supabase and appdb is not None:
            inst = appdb.supabase.get()
            try:
                if inst and inst["status"] == "running":
                    await asyncio.to_thread(appdb.connect, body.id, s.username, seed=body.supabase_seed, deploy=False)
                elif inst and inst["status"] == "installing":
                    appdb.queue([body.id], seed=body.supabase_seed)
            except AppDbError as exc:  # the app is created anyway: connect it from its page
                db.audit(s.username, "supabase-connect", False, service_id=body.id, detail=str(exc)[:300])
        rid = deployer.enqueue(body.id, "create", s.username)
        return app_payload(db.get_app(body.id), db) | {"release_id": rid}

    @app.post("/api/apps/adopt")
    async def adopt(body: AdoptService, s=Depends(current_session)) -> dict:
        svc = scanner.get(body.service_id)
        if svc is None:
            raise HTTPException(404, "unknown service")
        repo = repo_from_remote(svc.git_remote)
        if not repo or not svc.path:
            raise HTTPException(400, "this service has no GitHub remote")
        if svc.type == ServiceType.UNKNOWN:
            raise HTTPException(400, "set the service type first (Label → Type)")
        gh = require_github()
        try:
            installation = await asyncio.to_thread(gh.installation_for, repo)
        except GitHubError:
            raise HTTPException(400, f"install the GitHub App on {repo} first") from None
        app_id = body.id or svc.id.split(".")[0][:24].strip("-") or "app"
        branch = body.branch or svc.git_branch or "main"
        try:
            validate_app(app_id, repo, branch, None, svc.type.value)
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        if db.get_app(app_id):
            raise HTTPException(409, f"app {app_id} already exists, choose another id")
        git_root = svc.path
        while git_root not in ("", "/") and not host.exists(f"{git_root}/.git"):
            git_root = str(Path(git_root).parent).replace("\\", "/")
        db.create_app(app_id, name=svc.display_name or svc.name, repo=repo, branch=branch,
                      installation_id=installation, domain=svc.domains[0] if svc.domains else None,
                      type=svc.type.value, port=svc.port, mode="adopted", service_id=svc.id,
                      path=git_root if git_root not in ("", "/") else svc.path, auto_deploy=1)
        db.audit(s.username, "app-adopt", True, svc.id, detail=f"{app_id}: {repo}@{branch}")
        return app_payload(db.get_app(app_id), db)

    @app.get("/api/apps/{app_id}")
    def get_app(app_id: str, _=Depends(current_session)) -> dict:
        return app_payload(find_app(app_id), db) | {"releases": db.list_releases(app_id, 30)}

    @app.patch("/api/apps/{app_id}")
    def patch_app(app_id: str, body: PatchApp, s=Depends(current_session)) -> dict:
        current = find_app(app_id)
        fields = {}
        for key in body.model_fields_set:
            value = getattr(body, key)
            if isinstance(value, str):
                value = value.strip() or None
            fields[key] = int(value) if key == "auto_deploy" else value
        if "domain" in fields and fields["domain"]:
            fields["domain"] = fields["domain"].lower()
        if "root_dir" in fields:
            fields["root_dir"] = (fields["root_dir"] or "").strip("/")
        merged = current | fields
        try:
            validate_app(app_id, merged["repo"], merged["branch"] or "", merged["domain"], merged["type"])
            validate_commands(merged["build_command"], merged["start_command"], merged["root_dir"])
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        if current["mode"] == "adopted" and "domain" in fields:
            raise HTTPException(400, "the domain of an adopted service is managed by its own nginx vhost")
        if fields:
            db.update_app(app_id, **fields)
            db.audit(s.username, "app-edit", True, detail=f"{app_id}: {', '.join(fields)}")
        return app_payload(db.get_app(app_id), db)

    @app.get("/api/apps/{app_id}/env")
    def get_env(app_id: str, _=Depends(current_session)) -> dict:
        target = find_app(app_id)
        try:
            env = decrypt_env(load_fernet(host, config.secret_key_path), target["env"])
        except EnvError as exc:
            raise HTTPException(500, str(exc)) from None
        return {"text": "".join(f"{k}={v}\n" for k, v in sorted(env.items()))}

    @app.put("/api/apps/{app_id}/env")
    def put_env(app_id: str, body: EnvBody, s=Depends(current_session)) -> dict:
        find_app(app_id)
        try:
            env = parse_env(body.text)
        except EnvError as exc:
            raise HTTPException(400, str(exc)) from None
        token = encrypt_env(load_fernet(host, config.secret_key_path), env) if env else None
        db.update_app(app_id, env=token)
        db.audit(s.username, "app-env", True, detail=f"{app_id}: {len(env)} variable(s)")
        return {"ok": True, "count": len(env)}

    @app.put("/api/apps/{app_id}/scaling")
    def set_scaling(app_id: str, body: ScalingBody, s=Depends(current_session)) -> dict:
        """Load balancing: instances on this server and/or backends outside Mas, then redeploy."""
        current = find_app(app_id)
        if current["mode"] != "managed" or ServiceType(current["type"]) not in PROXY_TYPES or not current["port"]:
            raise HTTPException(400, "load balancing applies to Node.js, Next.js and Python apps deployed by Mas")
        try:
            backends = validate_backends(body.backends)
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        extra = [p for _, p in app_instances(current)][1:body.instances]
        if len(extra) < body.instances - 1:
            used = used_ports(host, db)
            free = [p for p in range(config.port_min, config.port_max + 1) if p not in used]
            if len(free) < body.instances - 1 - len(extra):
                raise HTTPException(409, "not enough free ports")
            extra += free[:body.instances - 1 - len(extra)]
        db.update_app(app_id, extra_ports=json.dumps(extra), backends=json.dumps(backends))
        db.audit(s.username, "app-scaling", True,
                 detail=f"{app_id}: {body.instances} instance(s), backends: {', '.join(backends) or 'none'}")
        rid = deployer.enqueue(app_id, "manual", s.username)
        return app_payload(db.get_app(app_id), db) | {"release_id": rid}

    @app.post("/api/apps/{app_id}/deploy")
    def deploy_now(app_id: str, s=Depends(current_session)) -> dict:
        find_app(app_id)
        return {"release_id": deployer.enqueue(app_id, "manual", s.username)}

    @app.post("/api/apps/{app_id}/rollback")
    def rollback(app_id: str, body: RollbackBody, s=Depends(current_session)) -> dict:
        find_app(app_id)
        try:
            rid = deployer.enqueue(app_id, "rollback", s.username, rollback_to=body.release_id)
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"release_id": rid}

    @app.delete("/api/apps/{app_id}")
    async def delete_app(app_id: str, purge: bool = False, s=Depends(current_session)) -> dict:
        find_app(app_id)
        try:
            done = await asyncio.to_thread(deployer.remove_app, app_id, purge)
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "app-delete", True, detail=f"{app_id}: {'; '.join(done) or 'unregistered'}")
        return {"ok": True, "done": done}

    # -- releases & logs -----------------------------------------------------

    def find_release(app_id: str, release_id: int) -> dict:
        rel = db.get_release(release_id)
        if rel is None or rel["app_id"] != app_id:
            raise HTTPException(404, "unknown release")
        return rel

    @app.get("/api/apps/{app_id}/releases/{release_id}/log", response_class=PlainTextResponse)
    def release_log(app_id: str, release_id: int, _=Depends(current_session)) -> str:
        find_release(app_id, release_id)
        path = deployer.log_path(app_id, release_id)
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    @app.get("/api/apps/{app_id}/releases/{release_id}/stream")
    async def release_stream(app_id: str, release_id: int, _=Depends(current_session)):
        find_release(app_id, release_id)
        path = deployer.log_path(app_id, release_id)

        async def events():
            pos, partial, last_beat = 0, "", time.monotonic()
            while True:
                chunk = ""
                if path.is_file():
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                if chunk:
                    lines = (partial + chunk).split("\n")
                    partial = lines.pop()
                    for line in lines:
                        yield f"data: {line}\n\n"
                status = (db.get_release(release_id) or {}).get("status", "failed")
                if status in FINAL_STATUSES and not chunk:
                    if partial:
                        yield f"data: {partial}\n\n"
                    yield f"event: end\ndata: {status}\n\n"
                    return
                if time.monotonic() - last_beat > 15:
                    last_beat = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
