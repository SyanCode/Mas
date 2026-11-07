"""Apps created by an AI agent (MCP): what it may create, and what it never sees.

An agent holding an API token with the ``create`` scope can plan and create an app from a repository
the administrator allowed (Settings → Claude). It never reads nor writes a secret: variables come from
the repository's ``.env.example`` (non-secret defaults only); the others stay missing and the app waits
(``apps.pending_env``) until a human fills them in the dashboard. DNS records are only ever added, never
replaced or deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from mas.auth import RateLimiter
from mas.config import Config
from mas.db import Database
from mas.deploy import (
    APP_ID_RE, BRANCH_RE, DOMAIN_RE, REPO_RE, DeployError, Deployer, domain_conflict, suggest_start_command,
    validate_commands,
)
from mas.dns import server_ips, zone_index
from mas.dnsproviders import DnsConflict, DnsError, Opener, point_domain
from mas.domains import check_name, names_in_use, point_www, www_of
from mas.envcrypt import EnvError
from mas.envdetect import ENV_EXAMPLE_FILES, MAX_ENV_EXAMPLE_BYTES, parse_env_example
from mas.github import GitHubApp, GitHubError
from mas.host import Host
from mas.models import ServiceType

SETTING = "mcp_allowed_repos"
PATTERN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/(\*|[A-Za-z0-9._-]{1,100})$")


class AgentError(Exception):
    pass


def allowed_repos(db: Database) -> list[str]:
    raw = db.get_setting(SETTING)
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [r for r in value if isinstance(r, str)]


def set_allowed_repos(db: Database, repos: list[str]) -> list[str]:
    clean: list[str] = []
    for item in repos:
        item = item.strip()
        if not item:
            continue
        if not PATTERN_RE.fullmatch(item):
            raise AgentError(f"invalid repository {item!r}: use Owner/Repo or Owner/*")
        if item.lower() not in (c.lower() for c in clean):
            clean.append(item)
    db.set_setting(SETTING, json.dumps(clean) if clean else None)
    return clean


def repo_allowed(db: Database, repo: str) -> bool:
    if not REPO_RE.fullmatch(repo or ""):
        return False
    owner = repo.split("/")[0].lower()
    for pattern in allowed_repos(db):
        p_owner, p_name = pattern.lower().split("/")
        if p_owner == owner and (p_name == "*" or f"{p_owner}/{p_name}" == repo.lower()):
            return True
    return False


# -- plan and create (routes /api/agent/*) -------------------------------------------------------------

log = logging.getLogger(__name__)

CREATES_PER_HOUR = 5
AGENT_TYPES = ("node", "nextjs", "static", "python", "php")
BUILD_DEFAULTS = {
    "node": "npm ci (npm install without a lock file), then npm run build if package.json has a build script",
    "nextjs": "npm ci, then npm run build",
    "python": "python3 -m venv .venv, then pip install -r requirements.txt (or pip install .)",
    "static": "none (npm ci && npm run build when package.json is there)",
    "php": "none (composer install when composer.json is there)",
}
START_DEFAULTS = {"node": "the start script of package.json", "nextjs": "next start",
                  "static": "none: nginx serves the files", "php": "none: nginx + PHP-FPM"}


class CreateBody(BaseModel):
    repo: str = Field(max_length=200)
    branch: str = Field(max_length=200)
    app_id: str = Field(max_length=24)
    domain: str | None = Field(default=None, max_length=253)
    type: str | None = Field(default=None, max_length=10)
    build_command: str | None = Field(default=None, max_length=1000)
    start_command: str | None = Field(default=None, max_length=1000)
    root_dir: str = Field(default="", max_length=200)
    auto_deploy: bool = True


def _slug(repo: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", repo.split("/")[-1].lower()).strip("-")[:24].strip("-")
    return name or "app"


def variables_of(text: str | None) -> tuple[list[dict], str, list[str]]:
    """(report without any value, env text Mas may fill itself, names a human must fill).

    Only non-secret variables with a usable value in the file are filled; a secret-looking one
    (PASS, TOKEN, SECRET, KEY) always waits for a human, even when the file gives it a value."""
    if not text:
        return [], "", []
    variables, _ignored = parse_env_example(text)
    report, lines, missing = [], [], []
    for v in variables:
        filled = bool(v.value) and not v.secret and not v.optional
        must = not v.optional and not filled
        report.append({"name": v.key, "description": v.description[:300], "required": must,
                       "optional": v.optional, "secret": v.secret, "has_default": filled})
        if filled:
            lines.append(f"{v.key}={json.dumps(v.value)}" if re.search(r"[\s#'\"]", v.value) else f"{v.key}={v.value}")
        if must:
            missing.append(v.key)
    return report, "\n".join(lines), missing


def register(app: FastAPI, *, host: Host, config: Config, db: Database, current_session: Callable,
             deployer: Deployer, github_client: Callable[[], GitHubApp | None], opener: Opener | None = None) -> None:
    creates = RateLimiter(max_hits=CREATES_PER_HOUR, window_seconds=3600)

    def agent_session(s=Depends(current_session)):
        if getattr(s, "scope", None) != "create":
            raise HTTPException(403, "creating apps needs an API token with the create scope (Settings → Claude)")
        return s

    def zone_for(name: str):
        index, _errors = zone_index(host, config, db, opener)
        best = None
        for zone, provider in index.items():
            if (name == zone or name.endswith("." + zone)) and (best is None or len(zone) > len(best[0])):
                best = (zone, provider)
        return best

    def domain_report(name: str, app_id: str) -> dict:
        """State of a domain, without changing anything."""
        out: dict = {"domain": name, "conflicts": [], "warnings": []}
        used = names_in_use(db).get(name)
        if used:
            out["conflicts"].append(f"{name} is already used by app {used}")
        served = domain_conflict(host, name, set())
        if served:
            out["conflicts"].append(f"{name} is already served by nginx ({served})")
        ipv4, ipv6 = server_ips(db, config)
        if not ipv4:
            out["warnings"].append("this server's public IPv4 is not set (Settings → Domains): no DNS record possible")
        found = zone_for(name)
        if found:
            zone, provider = found
            sub = name[: -len(zone)].rstrip(".")
            try:
                records = provider.records(zone, sub)
            except DnsError as exc:
                out["warnings"].append(f"cannot read the DNS records at {provider.label}: {exc}")
                records = []
            here = {ip for ip in (ipv4, ipv6) if ip}
            cname = [r for r in records if r["type"] == "CNAME"]
            elsewhere = [r for r in records if r["type"] in ("A", "AAAA") and r["target"] not in here]
            state = "cname" if cname else "points_elsewhere" if elsewhere else \
                "points_here" if records else "free"
            out.update(dns="registrar", registrar=provider.label, zone=zone, state=state,
                       records=[{"type": r["type"], "target": r["target"]} for r in records])
            if state == "cname":
                out["conflicts"].append(f"{name} is a CNAME to {cname[0]['target']}: Mas never replaces it")
            elif state == "points_elsewhere":
                targets = ", ".join(f"{r['type']} {r['target']}" for r in elsewhere)
                out["conflicts"].append(f"{name} already points to {targets}: Mas never replaces a record")
        else:
            check = check_name(name, None, (ipv4, ipv6), deployer.resolver)
            state = {"ok": "points_here", "missing": "free", "elsewhere": "points_elsewhere"}[check["status"]]
            out.update(dns="manual", state=state, resolves_to=check["ips"],
                       records_to_create=to_create(name, ipv4, ipv6))
            out["warnings"].append("no connected registrar manages this domain: a human must create the "
                                   "records_to_create at the registrar; check them with domain_status")
        return out

    def to_create(name: str, ipv4: str | None, ipv6: str | None) -> list[dict]:
        records = check_name(name, None, (ipv4, ipv6), lambda _n: [])["records"]
        www = www_of(name)
        if www:
            records += check_name(www, name, (ipv4, ipv6), lambda _n: [])["records"][:1]  # www CNAME → root
        return [{"name": r["name"], "type": r["type"], "value": r["value"]} for r in records]

    def plan(repo: str, branch: str, app_id: str | None, domain: str | None) -> dict:
        if not REPO_RE.fullmatch(repo or "") or ".." in repo:
            raise HTTPException(400, "invalid repository: use Owner/Name")
        if not BRANCH_RE.fullmatch(branch or ""):
            raise HTTPException(400, "invalid branch")
        if not repo_allowed(db, repo):
            raise HTTPException(403, f"{repo} is not in the repositories allowed for agents (Settings → Claude)")
        app_id = (app_id or _slug(repo)).strip().lower()
        out: dict = {"repo": repo, "branch": branch, "app_id": app_id, "conflicts": [], "warnings": []}
        if not APP_ID_RE.fullmatch(app_id):
            out["conflicts"].append(f"invalid app id {app_id!r}: 1-24 lowercase letters, digits and dashes")
        elif db.get_app(app_id):
            out["conflicts"].append(f"app id {app_id} is already taken")
        gh = github_client()
        if gh is None:
            raise HTTPException(409, "the GitHub App of Mas is not configured (Settings → GitHub)")
        try:
            installation = gh.installation_for(repo)
        except GitHubError:
            out["github"] = {"accessible": False}
            out["conflicts"].append(f"the GitHub App of Mas cannot read {repo}: install it on this repository")
            return out
        out["github"] = {"accessible": True, "installation_id": installation}
        try:
            if branch not in gh.branches(installation, repo):
                out["conflicts"].append(f"branch {branch} does not exist in {repo}")
            detected = gh.detect_type(installation, repo, branch)
            env_text = None
            for name in ENV_EXAMPLE_FILES:
                content, _ = gh.read_file(installation, repo, name, branch)
                if content:
                    out["env_file"] = name
                    env_text = content[:MAX_ENV_EXAMPLE_BYTES].decode("utf-8", errors="replace")
                    break
        except GitHubError:
            log.exception("GitHub API call failed")
            raise HTTPException(502, "GitHub API error") from None
        kind = detected.value
        out["type"] = kind if kind in AGENT_TYPES else None
        if out["type"] is None:
            out["warnings"].append("type not detected: pass type (node, nextjs, static, python, php)")
        files = {"manage.py"} if detected == ServiceType.PYTHON else set()
        out["build"] = BUILD_DEFAULTS.get(kind)
        out["start"] = START_DEFAULTS.get(kind) or suggest_start_command(detected, files)
        if detected == ServiceType.PYTHON:
            out["warnings"].append("python: check the start command; it must listen on 127.0.0.1:$PORT")
        report, _text, missing = variables_of(env_text)
        out["variables"] = report
        out["missing_variables"] = missing
        if missing:
            out["warnings"].append("the app will wait for these variables, filled by a human in the dashboard "
                                   f"(Apps → {app_id} → Variables): {', '.join(missing)}")
        if domain:
            name = domain.strip().lower()
            if not DOMAIN_RE.fullmatch(name):
                out["conflicts"].append(f"invalid domain {domain!r}")
            else:
                out["domain"] = domain_report(name, app_id)
                out["conflicts"] += out["domain"].pop("conflicts")
                out["warnings"] += out["domain"].pop("warnings")
        out["can_create"] = not out["conflicts"]
        return out

    @app.get("/api/agent/plan")
    async def plan_route(repo: str = Query(max_length=200), branch: str = Query(max_length=200),
                         app_id: str | None = Query(default=None, max_length=24),
                         domain: str | None = Query(default=None, max_length=253), _=Depends(agent_session)) -> dict:
        """What creating this app would do. Changes nothing."""
        return await asyncio.to_thread(plan, repo, branch, app_id, domain)

    @app.post("/api/agent/apps")
    async def create_route(body: CreateBody, s=Depends(agent_session)) -> dict:
        key = f"token:{s.token_id}"
        if creates.blocked(key):
            raise HTTPException(429, f"at most {CREATES_PER_HOUR} app creations per hour for a token")
        try:
            result = await asyncio.to_thread(create, body, s)
            creates.hit(key)  # counts the apps created; refusals are in the history
            return result
        except HTTPException as exc:
            db.audit(s.username, "app-create", False, service_id=None,
                     detail=f"{body.app_id}: {body.repo}@{body.branch}: {exc.detail}"[:500])
            raise

    def create(body: CreateBody, s) -> dict:
        found = plan(body.repo, body.branch, body.app_id, body.domain)
        if found["conflicts"]:
            raise HTTPException(409, "; ".join(found["conflicts"]))
        kind = (body.type or found.get("type") or "").strip().lower()
        if kind not in AGENT_TYPES:
            raise HTTPException(400, "type: one of node, nextjs, static, python, php")
        app_type = ServiceType(kind)
        start = body.start_command
        if app_type == ServiceType.PYTHON and not (start or "").strip():
            start = suggest_start_command(app_type, set())
        try:
            validate_commands(body.build_command, start, body.root_dir)
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        installation = found["github"]["installation_id"]
        env_text = None
        gh = github_client()
        try:
            for name in ENV_EXAMPLE_FILES:
                content, _ = gh.read_file(installation, body.repo, name, body.branch)
                if content:
                    env_text = content[:MAX_ENV_EXAMPLE_BYTES].decode("utf-8", errors="replace")
                    break
        except GitHubError:
            raise HTTPException(502, "GitHub API error") from None
        _report, defaults, missing = variables_of(env_text)
        domain = (body.domain or "").strip().lower() or None
        from mas.api_deploy import register_managed_app  # here: api_deploy imports this module's neighbours
        try:
            register_managed_app(host, config, db, app_id=found["app_id"], repo=body.repo, branch=body.branch,
                                 installation_id=installation, domain=domain, app_type=app_type, env=defaults,
                                 build_command=body.build_command, start_command=start, root_dir=body.root_dir,
                                 auto_deploy=body.auto_deploy)
        except (DeployError, EnvError) as exc:
            raise HTTPException(400, str(exc)) from None
        app_id = found["app_id"]
        if missing:
            db.update_app(app_id, pending_env=json.dumps(missing))
        dns = point(domain, found.get("domain") or {}) if domain else None
        db.audit(s.username, "app-create", True, service_id=app_id,
                 detail=f"{app_id}: {body.repo}@{body.branch} → {domain or 'no domain'}"
                 + (f"; waiting for {', '.join(missing)}" if missing else "")
                 + (f"; DNS: {'; '.join(dns.get('changes', []))}" if dns and dns.get("changes") else ""))
        release_id = None
        if not missing:
            release_id = deployer.enqueue(app_id, "create", s.username)
        nxt = (f"a human must fill {', '.join(missing)} in the dashboard (Apps → {app_id} → Variables); "
               "saving them starts the first deploy") if missing else "follow the first deploy with get_app"
        return {"app_id": app_id, "status": "waiting_for_variables" if missing else "deploying",
                "missing_variables": missing, "release_id": release_id, "dns": dns, "next": nxt}

    def point(name: str, report: dict) -> dict:
        ipv4, ipv6 = server_ips(db, config)
        if report.get("dns") != "registrar":
            return {"mode": "manual", "records_to_create": to_create(name, ipv4, ipv6),
                    "note": "ask a human to create these records at the registrar, then check with domain_status; "
                            "the certificate comes with the first deploy after the name resolves"}
        if not ipv4:
            return {"mode": "registrar", "changes": [], "error": "public IPv4 not set (Settings → Domains)"}
        found = zone_for(name)
        zone, provider = found
        sub = name[: -len(zone)].rstrip(".")
        try:  # never overwrite: plan() already refused a name pointing elsewhere
            changes = point_domain(provider, zone, sub, ipv4, ipv6, overwrite=False)["changes"]
        except (DnsConflict, DnsError) as exc:
            return {"mode": "registrar", "registrar": provider.label, "changes": [], "error": str(exc)}
        changes += point_www(provider, zone, sub, ipv4, ipv6, overwrite=False)
        return {"mode": "registrar", "registrar": provider.label, "changes": changes}
