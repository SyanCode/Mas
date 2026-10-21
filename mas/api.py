"""HTTP API and static dashboard. Listens on 127.0.0.1 only; nginx terminates TLS."""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mas import __version__, api_cms, api_deploy, api_dns, api_supabase, auth
from mas.actions import CONFIRM_ACTIONS, ActionError, available_actions, nginx_reload, run_action
from mas.deploy import Deployer
from mas.github import GitHubApp
from mas.cms import CmsManager
from mas.supabase import SupabaseManager
from mas.config import Config, load_config
from mas.db import Database
from mas.host import Host
from mas.logs import get_logs
from mas.models import Service, ServiceType
from mas.state import Scanner

log = logging.getLogger(__name__)

COOKIE = "mas_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TRUSTED_PROXIES = {"127.0.0.1", "::1"}
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self' https://github.com"  # GitHub App manifest form
    ),
}


# -- request bodies ----------------------------------------------------------


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)
    otp: str | None = Field(default=None, max_length=16)


class SignupBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)
    code: str | None = Field(default=None, max_length=200)
    invite: str | None = Field(default=None, max_length=100)  # token of a one-time link


class SignupSettings(BaseModel):
    open: bool
    code: str = Field(default="", max_length=200)


class PatchService(BaseModel):
    type: ServiceType | None = None  # None (explicit) = back to auto-detection
    display_name: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=4000)


class PasswordBody(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


class PasswordOnly(BaseModel):
    password: str = Field(max_length=1024)
    code: str | None = Field(default=None, max_length=16)  # required when 2FA is already on


class CodeBody(BaseModel):
    code: str = Field(max_length=16)


class DisableTotpBody(BaseModel):
    password: str = Field(max_length=1024)
    code: str = Field(max_length=16)


@dataclass
class SessionInfo:
    token_hash: str
    user_id: int
    username: str
    csrf: str
    totp_enabled: bool
    role: str = "admin"


def default_web_dir(config: Config) -> Path | None:
    candidates = [Path(config.web_dir)] if config.web_dir else []
    candidates += [Path(config.src_dir) / "web" / "out", Path(__file__).resolve().parent.parent / "web" / "out"]
    return next((p for p in candidates if (p / "index.html").is_file()), None)


def service_payload(svc: Service) -> dict:
    data = svc.model_dump(mode="json")
    data["actions"] = available_actions(svc)
    data["confirm_actions"] = sorted(CONFIRM_ACTIONS & set(data["actions"]))
    return data


def create_app(
    host: Host | None = None,
    config: Config | None = None,
    db: Database | None = None,
    background: bool = True,
    web_dir: Path | None = None,
    ovh_opener=None,  # tests: fake OVH API
) -> FastAPI:
    host = host or Host()
    config = config or load_config(host)
    db = db or Database(config.db_path)
    scanner = Scanner(host, config, db)
    ip_limiter = auth.RateLimiter(max_hits=10, window_seconds=15 * 60)
    user_limiter = auth.RateLimiter(max_hits=5, window_seconds=15 * 60)
    signup_limiter = auth.RateLimiter(max_hits=5, window_seconds=3600)
    session_ttl = config.session_hours * 3600
    # "__Host-" cookies are only accepted over HTTPS, for this exact host, path "/":
    # a sibling sub-domain cannot plant or overwrite the session.
    cookie_name = f"__Host-{COOKIE}" if config.cookie_secure else COOKIE

    async def scan_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(scanner.refresh)
            except Exception:
                log.exception("background scan failed")
            try:
                await asyncio.to_thread(supabase.auto_backup)
            except Exception:
                log.exception("Supabase backup failed")
            await asyncio.sleep(max(10, config.scan_interval))

    github_cache: dict = {}

    def github_client() -> GitHubApp | None:
        if not config.github_app_id:
            return None
        pem = host.read_bytes(config.github_private_key_path)
        if not pem:
            return None
        key = (config.github_app_id, hash(pem))
        if github_cache.get("key") != key:
            github_cache.update(key=key, client=GitHubApp(config.github_app_id, pem))
        return github_cache["client"]

    deployer = Deployer(host, config, db, github_client, find_service=scanner.get,
                        on_change=lambda: scanner.refresh())
    supabase = SupabaseManager(host, config, db, deployer)
    cms = CmsManager(db, github_client, deployer, Path(db.path).parent, supabase=supabase)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with db.tx() as c:  # a restart interrupts running deployments
            c.execute("UPDATE releases SET status = 'failed', error = 'interrupted (Mas restarted)' "
                      "WHERE status IN ('queued', 'building')")
        task = asyncio.create_task(scan_loop()) if background else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Mas", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.scanner, app.state.db, app.state.host = scanner, db, host
    app.state.cms = cms

    # -- middleware --------------------------------------------------------

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # -- dependencies ------------------------------------------------------

    def client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if peer in TRUSTED_PROXIES and request.headers.get("x-real-ip"):
            return request.headers["x-real-ip"]
        return peer

    def any_session(request: Request) -> SessionInfo:
        """Any signed-in user, CMS editors included."""
        token = request.cookies.get(cookie_name)
        if not token:
            raise HTTPException(401, "not authenticated")
        token_hash = auth.hash_token(token)
        row = db.get_session(token_hash)
        now = time.time()
        if row is None or row["expires_at"] < now:
            if row is not None:
                db.delete_session(token_hash)
            raise HTTPException(401, "session expired")
        db.touch_session(token_hash, min(now + session_ttl, row["created_at"] + 7 * 86400))
        session = SessionInfo(token_hash, row["user_id"], row["username"], row["csrf"], bool(row["totp_enabled"]),
                              row["role"])
        if request.method not in SAFE_METHODS:
            sent = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(sent, session.csrf):
                raise HTTPException(403, "invalid CSRF token")
        return session

    def current_session(request: Request) -> SessionInfo:
        """Administrators only: every route that touches the server uses this one."""
        session = any_session(request)
        if session.role != "admin":
            raise HTTPException(403, "administrators only")
        return session

    def find_service(service_id: str) -> Service:
        svc = scanner.get(service_id)
        if svc is None:
            raise HTTPException(404, f"unknown service {service_id}")
        return svc

    def session_payload(s: SessionInfo) -> dict:
        return {"username": s.username, "csrf": s.csrf, "totp_enabled": s.totp_enabled, "role": s.role,
                "version": __version__}

    # -- auth --------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}  # unauthenticated: no version disclosure

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict:
        ip, username = client_ip(request), body.username.strip().lower()
        if ip_limiter.blocked(ip) or user_limiter.blocked(username):
            raise HTTPException(429, "too many attempts, retry in 15 minutes")
        try:
            user = auth.authenticate(db, username, body.password, body.otp)
        except auth.AuthError as exc:
            if exc.code != "otp_required":
                ip_limiter.hit(ip)
                user_limiter.hit(username)
                db.audit(username or "?", "login", False, detail=f"{exc.code} from {ip}")
            raise HTTPException(401, exc.code) from None
        user_limiter.reset(username)
        new = auth.create_session(db, user["id"], session_ttl, ip)
        response.set_cookie(cookie_name, new.token, max_age=session_ttl, httponly=True,
                            secure=config.cookie_secure, samesite="strict", path="/")
        db.audit(username, "login", True, detail=f"from {ip}")
        return {"username": username, "csrf": new.csrf, "totp_enabled": bool(user["totp_secret"]),
                "role": user["role"], "version": __version__}

    # Self-service sign-up for CMS editors, when an administrator opened it. A new account
    # is an editor with no site: it sees nothing until an administrator grants access.

    def signup_state() -> tuple[bool, str]:
        return db.get_setting("signup_open") == "1", db.get_setting("signup_code") or ""

    @app.get("/api/signup")
    def signup_info(invite: str | None = Query(None, max_length=100)) -> dict:
        is_open, code = signup_state()
        valid = bool(invite) and db.valid_invite(auth.hash_token(invite)) is not None
        return {"open": is_open, "code_required": is_open and bool(code), "invite": valid if invite else None}

    @app.post("/api/signup")
    def signup(body: SignupBody, request: Request, response: Response) -> dict:
        ip = client_ip(request)
        is_open, code = signup_state()
        invite_hash = auth.hash_token(body.invite) if body.invite else None
        if invite_hash is None and not is_open:
            raise HTTPException(403, "sign-up is closed")
        if signup_limiter.blocked(ip):
            raise HTTPException(429, "too many sign-ups from this address, retry later")
        signup_limiter.hit(ip)
        if invite_hash is not None:
            if db.valid_invite(invite_hash) is None:
                raise HTTPException(403, "this invitation link was already used or has expired")
        elif code and not hmac.compare_digest((body.code or "").strip().encode(), code.encode()):
            db.audit(body.username[:32] or "?", "signup", False, detail=f"wrong code from {ip}")
            raise HTTPException(403, "wrong invitation code")
        username = body.username.strip().lower()
        if not auth.USERNAME_RE.fullmatch(username):
            raise HTTPException(400, "username: 2-32 characters, lowercase letters, digits, . _ -")
        if problem := auth.check_password_strength(body.password):
            raise HTTPException(400, problem)
        if db.get_user(username):
            raise HTTPException(409, "this username is taken")
        invite = None
        if invite_hash is not None:  # consumed only once everything else is valid
            invite = db.consume_invite(invite_hash, username)
            if invite is None:
                raise HTTPException(403, "this invitation link was already used or has expired")
        user_id = db.create_user(username, auth.hash_password(body.password), role="editor")
        if invite:
            db.set_user_apps(user_id, [a for a in invite["apps"] if db.get_app(a)])
        db.audit(username, "signup", True,
                 detail=f"from {ip}" + (f", invited by {invite['created_by']}" if invite else ""))
        new = auth.create_session(db, user_id, session_ttl, ip)
        response.set_cookie(cookie_name, new.token, max_age=session_ttl, httponly=True,
                            secure=config.cookie_secure, samesite="strict", path="/")
        return {"username": username, "csrf": new.csrf, "totp_enabled": False, "role": "editor",
                "version": __version__}

    @app.get("/api/signup/settings")
    def signup_settings(_: SessionInfo = Depends(current_session)) -> dict:
        is_open, code = signup_state()
        return {"open": is_open, "code": code}

    @app.put("/api/signup/settings")
    def set_signup_settings(body: SignupSettings, s: SessionInfo = Depends(current_session)) -> dict:
        db.set_setting("signup_open", "1" if body.open else None)
        db.set_setting("signup_code", body.code.strip() or None)
        db.audit(s.username, "signup-settings", True,
                 detail=("open" if body.open else "closed") + (" with a code" if body.code.strip() else ""))
        return {"open": body.open, "code": body.code.strip()}

    @app.post("/api/logout")
    def logout(response: Response, s: SessionInfo = Depends(any_session)) -> dict:
        db.delete_session(s.token_hash)
        response.delete_cookie(cookie_name, path="/", secure=config.cookie_secure, httponly=True,
                               samesite="strict")
        return {"ok": True}

    @app.get("/api/session")
    def session(s: SessionInfo = Depends(any_session)) -> dict:
        return session_payload(s)

    # -- services ----------------------------------------------------------

    @app.get("/api/services")
    def list_services(_: SessionInfo = Depends(current_session)) -> dict:
        res = scanner.result()
        return {"scanned_at": res.scanned_at.isoformat(), "warnings": res.warnings,
                "services": [service_payload(s) for s in res.services]}

    @app.get("/api/services/{service_id}")
    def get_service(service_id: str, _: SessionInfo = Depends(current_session)) -> dict:
        svc = find_service(service_id)
        return service_payload(svc) | {"history": db.history(service_id, 30)}

    @app.post("/api/scan")
    async def rescan(_: SessionInfo = Depends(current_session)) -> dict:
        res = await asyncio.to_thread(scanner.refresh)
        return {"scanned_at": res.scanned_at.isoformat(), "count": len(res.services), "warnings": res.warnings}

    @app.get("/api/services/{service_id}/logs")
    async def service_logs(service_id: str, lines: int = Query(200, ge=1, le=2000),
                           _: SessionInfo = Depends(current_session)) -> dict:
        svc = find_service(service_id)
        sources = await asyncio.to_thread(get_logs, host, svc, lines)
        return {"sources": [s.as_dict() for s in sources]}

    @app.post("/api/services/{service_id}/actions/{action}")
    async def service_action(service_id: str, action: str, s: SessionInfo = Depends(current_session)) -> dict:
        svc = find_service(service_id)
        try:
            result = await asyncio.to_thread(run_action, host, svc, action)
        except ActionError as exc:
            db.audit(s.username, action, False, service_id, detail=str(exc))
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, action, result.ok, service_id, result.command, result.output)
        asyncio.get_running_loop().run_in_executor(None, scanner.refresh)
        return result.as_dict()

    @app.patch("/api/services/{service_id}")
    def patch_service(service_id: str, body: PatchService, s: SessionInfo = Depends(current_session)) -> dict:
        find_service(service_id)
        fields = {}
        sent = body.model_fields_set
        if "type" in sent:
            fields["type_override"] = body.type.value if body.type else None
        if "display_name" in sent:
            fields["display_name"] = (body.display_name or "").strip() or None
        if "notes" in sent:
            fields["notes"] = (body.notes or "").strip() or None
        if not fields:
            raise HTTPException(400, "nothing to update")
        db.set_meta(service_id, **fields)
        db.audit(s.username, "edit", True, service_id, detail=", ".join(f"{k}={v}" for k, v in fields.items()))
        if "type_override" in fields:
            scanner.refresh()
        else:
            scanner.reapply_meta()
        return service_payload(find_service(service_id))

    @app.post("/api/nginx/reload")
    async def reload_nginx(s: SessionInfo = Depends(current_session)) -> dict:
        result = await asyncio.to_thread(nginx_reload, host)
        db.audit(s.username, "nginx-reload", result.ok, None, result.command, result.output)
        if not result.ok:
            raise HTTPException(409, result.output)
        return result.as_dict()

    @app.get("/api/history")
    def history(limit: int = Query(100, ge=1, le=1000), _: SessionInfo = Depends(current_session)) -> list[dict]:
        return db.history(None, limit)

    # -- account -----------------------------------------------------------

    def require_password(s: SessionInfo, password: str):
        # Same budget as the login form: a stolen session cannot brute-force the password.
        key = f"session-password:{s.user_id}"
        if user_limiter.blocked(key):
            raise HTTPException(429, "too many attempts, retry in 15 minutes")
        user = db.get_user_by_id(s.user_id)
        if not auth.verify_password(user["password_hash"], password):
            user_limiter.hit(key)
            raise HTTPException(403, "wrong password")
        return user

    @app.post("/api/account/password")
    def change_password(body: PasswordBody, s: SessionInfo = Depends(any_session)) -> dict:
        user = require_password(s, body.current)
        if problem := auth.check_password_strength(body.new):
            raise HTTPException(400, problem)
        db.update_user(user["id"], password_hash=auth.hash_password(body.new))
        db.delete_user_sessions(user["id"], keep=s.token_hash)
        db.audit(s.username, "password-change", True)
        return {"ok": True}

    @app.post("/api/account/totp/setup")
    def totp_setup(body: PasswordOnly, s: SessionInfo = Depends(any_session)) -> dict:
        user = require_password(s, body.password)
        # Replacing the authenticator needs the current one: password + session are not enough.
        if user["totp_secret"] and not auth.verify_totp(db, user, body.code or ""):
            raise HTTPException(403, "enter the current code of your authenticator app")
        secret = auth.new_totp_secret()
        db.update_user(user["id"], totp_pending=secret)
        uri = auth.provisioning_uri(secret, s.username)
        return {"secret": secret, "uri": uri, "qr_svg": auth.qr_svg(uri)}

    @app.post("/api/account/totp/enable")
    def totp_enable(body: CodeBody, s: SessionInfo = Depends(any_session)) -> dict:
        user = db.get_user_by_id(s.user_id)
        pending = user["totp_pending"]
        if not pending:
            raise HTTPException(400, "start the setup first")
        step = auth.match_totp(pending, body.code)
        if step is None:
            raise HTTPException(400, "wrong code, check your phone's clock")
        db.update_user(user["id"], totp_secret=pending, totp_pending=None, totp_last_step=step)
        db.delete_user_sessions(user["id"], keep=s.token_hash)
        db.audit(s.username, "2fa-enable", True)
        return {"ok": True}

    @app.post("/api/account/totp/disable")
    def totp_disable(body: DisableTotpBody, s: SessionInfo = Depends(any_session)) -> dict:
        user = require_password(s, body.password)
        if not user["totp_secret"] or not auth.verify_totp(db, user, body.code):
            raise HTTPException(403, "wrong code")
        db.update_user(user["id"], totp_secret=None, totp_pending=None, totp_last_step=0)
        db.audit(s.username, "2fa-disable", True)
        return {"ok": True}

    app.state.deployer = deployer
    api_deploy.register(app, host=host, config=config, db=db, scanner=scanner, current_session=current_session,
                        deployer=deployer, github_client=github_client, reset_github=github_cache.clear)
    api_dns.register(app, host=host, config=config, db=db, current_session=current_session, deployer=deployer,
                     opener=ovh_opener)
    api_supabase.register(app, host=host, db=db, current_session=current_session, deployer=deployer,
                          manager=supabase)
    api_cms.register(app, db=db, any_session=any_session, current_session=current_session, manager=cms)

    # -- dashboard ---------------------------------------------------------

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    def api_not_found(path: str) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    static_dir = web_dir or default_web_dir(config)
    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:
        @app.get("/")
        def no_dashboard() -> JSONResponse:
            return JSONResponse({"detail": "dashboard not built (cd web && npm ci && npm run build)"},
                                status_code=503)

    return app
