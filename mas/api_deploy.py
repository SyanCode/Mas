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
    app_instances, replacement_plan, suggest_start_command, used_ports, validate_app, validate_backends,
    validate_commands,
)
from mas.dns import server_ips
from mas.domains import aliases_for, check_app
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
MAX_WEBHOOK_BYTES = 25 * 1024 * 1024  # GitHub's maximum webhook payload size


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
    replace_service: str | None = Field(default=None, max_length=200)  # a detected site the app takes over


class HealthFix(BaseModel):
    action: str = Field(max_length=40)
    target: str = Field(max_length=253)


class AdoptService(BaseModel):
    service_id: str = Field(max_length=200)
    id: str | None = Field(default=None, max_length=24)
    branch: str | None = Field(default=None, max_length=200)


class PatchApp(BaseModel):
    branch: str | None = Field(default=None, max_length=200)
    type: ServiceType | None = None
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


class FreezeBody(BaseModel):
    frozen: bool


class FreezeAppsBody(BaseModel):
    app_ids: list[str] = Field(max_length=500)
    frozen: bool


FREEZE_SETTING = "deploy_freeze"  # "1": no push deploys anything, every app keeps its commits waiting


def deploys_frozen(db: Database) -> bool:
    return db.get_setting(FREEZE_SETTING) == "1"


def register_managed_app(host: Host, config: Config, db: Database, *, app_id: str, repo: str, branch: str,
                         installation_id: int | None, domain: str | None, app_type: ServiceType, env: str = "",
                         build_command: str | None = None, start_command: str | None = None, root_dir: str = "",
                         auto_deploy: bool = True, aliases: list[str] | None = None, replaces: str | None = None,
                         project: str | None = None) -> None:
    """Validate and record an app Mas builds from GitHub (DeployError / EnvError when refused)."""
    root_dir = root_dir.strip("/")
    validate_app(app_id, repo, branch, domain, app_type.value)
    validate_commands(build_command, start_command, root_dir)
    variables = parse_env(env)
    if db.get_app(app_id):
        raise DeployError(f"app {app_id} already exists")
    if app_type == ServiceType.PYTHON and not (start_command or "").strip():
        raise DeployError("python apps need a start command")
    if aliases is None:  # a root domain also answers to its www
        aliases = aliases_for(db, app_id, domain)
    port = allocate_port(host, config, db) if app_type in PROXY_TYPES else None
    token = encrypt_env(load_fernet(host, config.secret_key_path), variables) if variables else None
    db.create_app(app_id, name=app_id, repo=repo, branch=branch, installation_id=installation_id, domain=domain,
                  type=app_type.value, port=port, mode="managed", root_dir=root_dir,
                  build_command=(build_command or "").strip() or None,
                  start_command=(start_command or "").strip() or None,
                  env=token, auto_deploy=int(auto_deploy),
                  aliases=json.dumps(aliases) if aliases else None, replaces=replaces, project=project)


def app_payload(app: dict, db: Database) -> dict:
    data = {k: v for k, v in app.items() if k != "env"}
    data["auto_deploy"] = bool(app["auto_deploy"])
    data["pending"] = json.loads(app["pending"]) if app.get("pending") else None
    data["has_env"] = bool(app["env"])
    data["instances"] = len(app_instances(app)) or 1
    data["instance_ports"] = [p for _, p in app_instances(app)]
    data["backends"] = app_backends(app)
    data["aliases"] = json.loads(app.get("aliases") or "[]")
    data["cluster"] = json.loads(app.get("cluster") or "[]")
    data["cluster_backends"] = json.loads(app.get("cluster_backends") or "[]")
    data["replaces"] = json.loads(app["replaces"]) if app.get("replaces") else None
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
    projects=None,
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
            log.exception("GitHub API call failed")
            raise HTTPException(502, "GitHub API error") from None

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
        # Do not trust Content-Length: chunked requests can omit it or lie.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "webhook payload too large")
        body = bytes(body)
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
            head = payload.get("head_commit") or {}
            commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
            deployed, waiting = [], []
            frozen_all = deploys_frozen(db)
            for target in db.apps_for_push(repo, branch, frozen=True):
                if target["mode"] == "stack":
                    continue
                if target["auto_deploy"] and not frozen_all:
                    deployer.enqueue(target["id"], "push", f"github:{actor}", sha)
                    deployed.append(target["id"])
                    continue
                # Frozen: nothing is built; the dashboard shows what waits and deploys it on demand.
                before = json.loads(target["pending"]) if target.get("pending") else {}
                message = (head.get("message") or "").splitlines()
                db.update_app(target["id"], pending=json.dumps({
                    "sha": sha, "message": message[0][:200] if message else "",
                    "author": ((head.get("author") or {}).get("name") or actor)[:100],
                    "commits": int(before.get("commits", 0)) + max(len(commits), 1),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
                waiting.append(target["id"])
            synced = projects.on_push(repo, branch, payload) if projects is not None else []
            detail = f"{repo}@{branch} {sha[:7]} → {', '.join(deployed) or 'no app'}"
            if waiting:
                detail += f"; frozen: {', '.join(waiting)}"
            if synced:
                detail += f"; project updated: {', '.join(synced)}"
            db.audit(f"github:{actor}", "webhook-push", True, detail=detail)
            return {"ok": True, "deployed": deployed, "frozen": waiting}
        if event == "pull_request":
            # Preview deployments are not implemented yet; the event is acknowledged.
            return {"ok": True, "ignored": "pull_request"}
        return {"ok": True, "ignored": event}

    # -- apps --------------------------------------------------------------

    @app.get("/api/apps")
    def list_apps(_=Depends(current_session)) -> list[dict]:
        return [app_payload(a, db) for a in db.list_apps()]

    def plan_for(service_id: str) -> tuple[str, list[str], list[str]]:
        svc = scanner.get(service_id)
        if svc is None:
            raise HTTPException(404, "unknown service")
        try:
            return replacement_plan(host, svc, db.list_apps())
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/services/{service_id}/replacement")
    def replacement(service_id: str, _=Depends(current_session)) -> dict:
        """What an app replacing this site would take over (shown before creating it)."""
        domain, aliases, files = plan_for(service_id)
        svc = scanner.get(service_id)
        return {"domain": domain, "aliases": aliases, "files": files, "repo": repo_from_remote(svc.git_remote),
                "branch": svc.git_branch, "type": svc.type.value, "path": svc.path}

    @app.post("/api/apps")
    async def create_app(body: CreateApp, s=Depends(current_session)) -> dict:
        domain = (body.domain or "").strip().lower() or None
        aliases: list[str] | None = None
        replaces = None
        if body.replace_service:
            domain, aliases, files = plan_for(body.replace_service)
            replaces = json.dumps({"service_id": body.replace_service, "files": files})
        if db.get_app(body.id):
            raise HTTPException(409, f"app {body.id} already exists")
        try:
            register_managed_app(host, config, db, app_id=body.id, repo=body.repo, branch=body.branch,
                                 installation_id=body.installation_id, domain=domain, app_type=body.type,
                                 env=body.env, build_command=body.build_command, start_command=body.start_command,
                                 root_dir=body.root_dir, auto_deploy=body.auto_deploy, aliases=aliases,
                                 replaces=replaces)
        except (DeployError, EnvError) as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "app-create", True, detail=f"{body.id}: {body.repo}@{body.branch} → {domain}"
                 + (f" (replaces {body.replace_service})" if replaces else ""))
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

    async def adoption_plan(body: AdoptService) -> dict:
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
        git_root = git_root if git_root not in ("", "/") else svc.path
        return {"service": svc, "repo": repo, "installation": installation, "app_id": app_id,
                "branch": branch, "path": git_root}

    @app.post("/api/apps/adopt/preview")
    async def preview_adoption(body: AdoptService, _=Depends(current_session)) -> dict:
        plan = await adoption_plan(body)
        svc = plan["service"]
        return {"service_id": svc.id, "app_id": plan["app_id"], "repo": plan["repo"],
                "branch": plan["branch"], "type": svc.type.value, "path": plan["path"],
                "manager": svc.manager.value, "status": svc.status.value, "domains": svc.domains,
                "effects": ["Register this existing checkout with Mas; files are not copied or moved.",
                            "A future deploy or push updates this checkout in place, builds it, and restarts its service.",
                            "Mas refuses deployment while the checkout has uncommitted tracked changes."]}

    @app.post("/api/apps/adopt")
    async def adopt(body: AdoptService, s=Depends(current_session)) -> dict:
        plan = await adoption_plan(body)
        svc, repo, installation = plan["service"], plan["repo"], plan["installation"]
        app_id, branch, git_root = plan["app_id"], plan["branch"], plan["path"]
        db.create_app(app_id, name=svc.display_name or svc.name, repo=repo, branch=branch,
                      installation_id=installation, domain=svc.domains[0] if svc.domains else None,
                      type=svc.type.value, port=svc.port, mode="adopted", service_id=svc.id,
                      path=git_root, auto_deploy=1)
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
            if key == "type":
                if value is None or value == ServiceType.UNKNOWN:
                    raise HTTPException(400, "choose a type")
                if current["mode"] != "managed":
                    raise HTTPException(400, "only the type of an app deployed by Mas can change")
                fields["type"] = value.value
                if value in PROXY_TYPES and not current["port"]:
                    fields["port"] = allocate_port(host, config, db)
                continue
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
        if "domain" in fields and fields["domain"] != current["domain"]:
            aliases = aliases_for(db, app_id, fields["domain"], json.loads(current.get("aliases") or "[]"),
                                  old_domain=current["domain"])
            fields["aliases"] = json.dumps(aliases) if aliases else None
        if fields:
            db.update_app(app_id, **fields)
            db.audit(s.username, "app-edit", True, detail=f"{app_id}: {', '.join(fields)}")
        return app_payload(db.get_app(app_id), db)

    @app.get("/api/apps/{app_id}/domains")
    async def app_domains(app_id: str, _=Depends(current_session)) -> dict:
        """Like Vercel: does each name of the app point to this server, and which records to add."""
        target = find_app(app_id)
        expected = server_ips(db, config)
        checks = await asyncio.to_thread(check_app, target, expected, deployer.resolver)
        return {"ipv4": expected[0], "ipv6": expected[1], "domains": checks}

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

    @app.post("/api/apps/deploy-all")
    def deploy_all(include_frozen: bool = False, s=Depends(current_session)) -> dict:
        """Queue a deployment of every app from GitHub; they run one after the other. Frozen apps stay
        as they are unless ``include_frozen``."""
        queued = [{"app_id": a, "release_id": deployer.enqueue(a, "manual", s.username)}
                  for a in deployer.redeploy_targets(include_frozen)]
        db.audit(s.username, "deploy-all", True,
                 detail=", ".join(q["app_id"] for q in queued) or "nothing to deploy")
        return {"queued": queued}

    # -- freeze: pushes build nothing ------------------------------------------

    @app.get("/api/deploy/freeze")
    def get_freeze(_=Depends(current_session)) -> dict:
        apps = [a for a in db.list_apps() if a["mode"] != "stack"]
        return {"frozen": deploys_frozen(db), "frozen_apps": [a["id"] for a in apps if not a["auto_deploy"]],
                "waiting": [a["id"] for a in apps if a.get("pending")]}

    @app.put("/api/deploy/freeze")
    def set_freeze(body: FreezeBody, s=Depends(current_session)) -> dict:
        """Freeze every app at once: pushes are recorded, nothing builds until it is lifted."""
        db.set_setting(FREEZE_SETTING, "1" if body.frozen else None)
        db.audit(s.username, "deploy-freeze" if body.frozen else "deploy-unfreeze", True, detail="every app")
        return get_freeze()

    @app.post("/api/apps/freeze")
    def freeze_apps(body: FreezeAppsBody, s=Depends(current_session)) -> dict:
        """Freeze or unfreeze several apps (a folder, a project, a selection)."""
        changed = []
        for app_id in dict.fromkeys(body.app_ids):
            target = db.get_app(app_id)
            if target is None or target["mode"] == "stack":
                continue
            if bool(target["auto_deploy"]) == body.frozen:
                db.update_app(app_id, auto_deploy=int(not body.frozen))
                changed.append(app_id)
        if changed:
            db.audit(s.username, "app-freeze" if body.frozen else "app-unfreeze", True, detail=", ".join(changed))
        return {"changed": changed}

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
