"""Projects: several repositories deployed as one product (a landing page, a dashboard, an API…).

Every repository stays an app of its own, with its domain, build and releases. The project links them:

- ``mas.project.json``, in one of the repositories (or composed in the dashboard and exported there),
  lists the apps, the variables they share and the order they deploy in::

    {
      "name": "Klarfi",
      "env": {"NEXT_PUBLIC_BRAND": "Klarfi"},
      "apps": [
        {"id": "klarfi-dashboard", "repo": "SyanCode/klarfi-dashboard", "domain": "app.klarfi.com"},
        {"id": "klarfi-landing", "repo": "SyanCode/klarfi-landing", "domain": "klarfi.com",
         "depends_on": ["klarfi-dashboard"],
         "env": {"NEXT_PUBLIC_APP_URL": "${apps.klarfi-dashboard.url}"}}
      ]
    }

- a value may name another app of the project: ``${apps.<id>.url}`` (https://its-domain, or its local
  address without a domain), ``${apps.<id>.domain}``, ``${apps.<id>.internal_url}``
  (http://127.0.0.1:port, for calls between apps of this server) and ``${apps.<id>.port}``;
- the variables reach the build and the running app, under the app's own variables (which win), so
  secrets stay in Mas, encrypted, and never in the repository;
- deploying the project builds the apps one after the other, each after those it ``depends_on``;
- a push changing ``mas.project.json`` on the branch it was read from updates the project.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from mas.db import Database
from mas.deploy import APP_ID_RE, BRANCH_RE, DOMAIN_RE, REPO_RE
from mas.envcrypt import ENV_KEY_RE
from mas.github import GitHubApp, GitHubError
from mas.models import ServiceType

log = logging.getLogger(__name__)

MANIFEST_FILE = "mas.project.json"
MAX_APPS = 20
MAX_VALUE = 4096
REF_RE = re.compile(r"\$\{apps\.([a-z0-9-]+)\.(url|domain|internal_url|port)\}")
APP_KEYS = {"id", "repo", "branch", "domain", "type", "root_dir", "build_command", "start_command", "env",
            "depends_on"}


class ProjectError(Exception):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:24].strip("-")


def _env(raw: Any, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProjectError(f"{where}: env must be an object of names and values")
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key):
            raise ProjectError(f"{where}: invalid variable name {key!r}")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProjectError(f"{where}: the value of {key} must be text")
        value = str(value)
        if len(value) > MAX_VALUE or "\n" in value or "\x00" in value:
            raise ProjectError(f"{where}: the value of {key} is too long or spans several lines")
        out[key] = value
    return out


def _text(entry: dict, key: str, where: str, limit: int) -> str | None:
    value = entry.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit or "\n" in value:
        raise ProjectError(f"{where}: invalid {key}")
    return value.strip() or None


def parse_manifest(data: Any) -> dict:
    """Validate a mas.project.json (text or parsed) and return it normalized."""
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise ProjectError(f"{MANIFEST_FILE} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ProjectError(f"{MANIFEST_FILE} must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        raise ProjectError("the project needs a name (60 characters at most)")
    project_id = data.get("id") or slug(name)
    if not isinstance(project_id, str) or not APP_ID_RE.fullmatch(project_id):
        raise ProjectError(f"invalid project id {project_id!r}: lowercase letters, digits and dashes")
    apps_raw = data.get("apps")
    if not isinstance(apps_raw, list) or not apps_raw:
        raise ProjectError("the project lists no app")
    if len(apps_raw) > MAX_APPS:
        raise ProjectError(f"a project has {MAX_APPS} apps at most")
    apps: list[dict] = []
    for i, entry in enumerate(apps_raw):
        where = f"apps[{i}]"
        if not isinstance(entry, dict):
            raise ProjectError(f"{where} must be an object")
        unknown = set(entry) - APP_KEYS
        if unknown:
            raise ProjectError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
        repo = entry.get("repo")
        if not isinstance(repo, str) or not REPO_RE.fullmatch(repo) or ".." in repo:
            raise ProjectError(f"{where}: invalid repo {repo!r} (owner/name)")
        app_id = entry.get("id") or slug(repo.split("/")[1])
        if not isinstance(app_id, str) or not APP_ID_RE.fullmatch(app_id):
            raise ProjectError(f"{where}: invalid id {app_id!r}")
        where = f"app {app_id}"
        branch = _text(entry, "branch", where, 200)
        if branch is not None and not BRANCH_RE.fullmatch(branch):
            raise ProjectError(f"{where}: invalid branch {branch!r}")
        domain = _text(entry, "domain", where, 253)
        if domain is not None:
            domain = domain.lower()
            if not DOMAIN_RE.fullmatch(domain):
                raise ProjectError(f"{where}: invalid domain {domain!r}")
        app_type = _text(entry, "type", where, 20)
        if app_type is not None and app_type not in {t.value for t in ServiceType} - {"unknown"}:
            raise ProjectError(f"{where}: invalid type {app_type!r}")
        depends = entry.get("depends_on") or []
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            raise ProjectError(f"{where}: depends_on must be a list of app ids")
        apps.append({
            "id": app_id, "repo": repo, "branch": branch, "domain": domain, "type": app_type,
            "root_dir": (_text(entry, "root_dir", where, 200) or "").strip("/"),
            "build_command": _text(entry, "build_command", where, 1000),
            "start_command": _text(entry, "start_command", where, 1000),
            "env": _env(entry.get("env"), where), "depends_on": list(dict.fromkeys(depends)),
        })
    ids = [a["id"] for a in apps]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ProjectError(f"app id used twice: {', '.join(duplicates)}")
    domains = [a["domain"] for a in apps if a["domain"]]
    if len(domains) != len(set(domains)):
        raise ProjectError("two apps of the project have the same domain")
    manifest = {"name": name.strip(), "id": project_id, "env": _env(data.get("env"), "env"), "apps": apps}
    for a in apps:
        for dep in a["depends_on"]:
            if dep not in ids or dep == a["id"]:
                raise ProjectError(f"app {a['id']}: depends_on names an unknown app {dep!r}")
    for where, env in [("env", manifest["env"]), *((f"app {a['id']}", a["env"]) for a in apps)]:
        for value in env.values():
            for ref, _ in REF_RE.findall(value):
                if ref not in ids:
                    raise ProjectError(f"{where}: ${{apps.{ref}…}} names an app that is not in the project")
    deploy_order(manifest)  # refuses a cycle
    return manifest


def deploy_order(manifest: dict) -> list[str]:
    """App ids, every app after those it depends on (file order otherwise)."""
    apps = {a["id"]: a for a in manifest["apps"]}
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(app_id: str, path: list[str]) -> None:
        if state.get(app_id) == 2:
            return
        if state.get(app_id) == 1:
            raise ProjectError(f"the apps depend on each other in a loop: {' → '.join([*path, app_id])}")
        state[app_id] = 1
        for dep in apps[app_id]["depends_on"]:
            visit(dep, [*path, app_id])
        state[app_id] = 2
        order.append(app_id)

    for app_id in apps:
        visit(app_id, [])
    return order


def dump_manifest(manifest: dict) -> str:
    """mas.project.json as written to a repository: empty fields left out."""
    apps = []
    for a in manifest["apps"]:
        apps.append({k: v for k, v in a.items() if v not in (None, "", [], {})})
    out: dict[str, Any] = {"name": manifest["name"]}
    if manifest["id"] != slug(manifest["name"]):
        out["id"] = manifest["id"]
    if manifest.get("env"):
        out["env"] = manifest["env"]
    out["apps"] = apps
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _app_value(app: dict | None, field: str) -> str:
    if not app:
        return ""
    local = f"http://127.0.0.1:{app['port']}" if app.get("port") else ""
    if field == "domain":
        return app.get("domain") or ""
    if field == "port":
        return str(app["port"]) if app.get("port") else ""
    if field == "internal_url":
        return local
    return f"https://{app['domain']}" if app.get("domain") else local


def resolve(value: str, apps: dict[str, dict]) -> str:
    return REF_RE.sub(lambda m: _app_value(apps.get(m.group(1)), m.group(2)), value)


def project_env(db: Database, app: dict) -> dict[str, str]:
    """Variables the project of ``app`` gives it (shared ones, then its own), references resolved."""
    if not app.get("project"):
        return {}
    project = db.get_project(app["project"])
    if project is None:
        return {}
    manifest = json.loads(project["manifest"])
    entry = next((a for a in manifest["apps"] if a["id"] == app["id"]), None)
    env = {**manifest.get("env", {}), **(entry["env"] if entry else {})}
    if not env:
        return {}
    ids = {a["id"] for a in manifest["apps"]}
    apps = {a["id"]: a for a in db.list_apps() if a["id"] in ids}
    return {k: resolve(v, apps) for k, v in env.items()}


class Projects:
    def __init__(self, db: Database, github: Callable[[], GitHubApp | None]) -> None:
        self.db = db
        self.github = github

    def read(self, installation_id: int, repo: str, branch: str) -> dict:
        """The mas.project.json of a repository, validated."""
        gh = self.github()
        if gh is None:
            raise ProjectError("GitHub App not configured (Settings → GitHub)")
        try:
            content, _ = gh.read_file(installation_id, repo, MANIFEST_FILE, branch)
        except GitHubError as exc:
            raise ProjectError(str(exc)) from None
        if content is None:
            raise ProjectError(f"no {MANIFEST_FILE} at the root of {repo}@{branch}")
        manifest = parse_manifest(content)
        for a in manifest["apps"]:  # an app of the repository holding the file follows its branch
            if a["branch"] is None and a["repo"].lower() == repo.lower():
                a["branch"] = branch
        return manifest

    def on_push(self, repo: str, branch: str, payload: dict) -> list[str]:
        """A push touching mas.project.json where a project was read from: read it again."""
        touched = any(MANIFEST_FILE in (c.get("added", []) + c.get("modified", []))
                      for c in payload.get("commits") or [] if isinstance(c, dict))
        if not touched:
            return []
        updated = []
        for project in self.db.list_projects():
            source = json.loads(project["source"]) if project["source"] else None
            if not source or source["repo"].lower() != repo.lower() or source["branch"] != branch:
                continue
            try:
                manifest = self.read(source["installation_id"], source["repo"], branch)
            except ProjectError as exc:
                self.db.audit("github", "project-sync", False, detail=f"{project['id']}: {exc}"[:500])
                continue
            manifest["id"] = project["id"]  # the id is the key: renaming happens in the dashboard
            self.db.save_project(project["id"], manifest["name"], json.dumps(manifest), project["source"])
            self.db.audit("github", "project-sync", True, detail=f"{project['id']}: {repo}@{branch}")
            updated.append(project["id"])
        return updated

    def export(self, project_id: str, installation_id: int, repo: str, branch: str, actor: str) -> str:
        """Commit the project's mas.project.json to ``repo``@``branch``; returns the commit sha."""
        project = self.db.get_project(project_id)
        if project is None:
            raise ProjectError(f"unknown project {project_id}")
        gh = self.github()
        if gh is None:
            raise ProjectError("GitHub App not configured (Settings → GitHub)")
        text = dump_manifest(json.loads(project["manifest"]))
        try:
            head = gh.head(installation_id, repo, branch)
            current, _ = gh.read_file(installation_id, repo, MANIFEST_FILE, head)
            if current is not None and current.decode("utf-8", "replace") == text:
                sha = head
            else:
                sha = gh.commit_files(installation_id, repo, branch, {MANIFEST_FILE: text.encode()},
                                      f"Mas: project {project['name']} ({MANIFEST_FILE})", head)
        except GitHubError as exc:
            raise ProjectError(str(exc)) from None
        source = json.dumps({"repo": repo, "branch": branch, "installation_id": installation_id})
        self.db.save_project(project_id, project["name"], project["manifest"], source)
        self.db.audit(actor, "project-export", True, detail=f"{project_id} → {repo}@{branch} {sha[:7]}")
        return sha
