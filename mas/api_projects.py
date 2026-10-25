"""API routes of the projects (mas.projects) and of the folders the dashboard sorts apps and services in.

Registered by ``mas.api.create_app``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from mas.api_deploy import app_payload, register_managed_app
from mas.config import Config
from mas.db import FOLDER_COLORS, FOLDER_KINDS, Database
from mas.deploy import BRANCH_RE, REPO_RE, DeployError, Deployer
from mas.envcrypt import EnvError
from mas.github import GitHubError
from mas.host import Host
from mas.models import ServiceType
from mas.projects import MANIFEST_FILE, ProjectError, Projects, deploy_order, dump_manifest, parse_manifest
from mas.state import Scanner

log = logging.getLogger(__name__)

MAX_FOLDERS = 500
MAX_DEPTH = 8


class SourceBody(BaseModel):
    repo: str = Field(max_length=200)
    branch: str = Field(max_length=200)
    installation_id: int


class SaveProject(BaseModel):
    manifest: dict
    source: SourceBody | None = None
    create_missing: bool = True  # apps of the file not in Mas yet are created
    deploy: bool = True          # then every app of the project is deployed, in order


class DeployProject(BaseModel):
    include_frozen: bool = True


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    parent_id: int | None = None
    color: str | None = Field(default=None, max_length=20)


class PatchFolder(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    parent_id: int | None = None
    color: str | None = Field(default=None, max_length=20)
    position: int | None = Field(default=None, ge=0, le=10_000)


class FolderItem(BaseModel):
    kind: str = Field(max_length=10)
    id: str = Field(max_length=200)


class MoveItems(BaseModel):
    items: list[FolderItem] = Field(max_length=500)
    folder_id: int | None = None


class SortFolder(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict)  # type -> folder name, in the language of the user


def register(
    app: FastAPI,
    *,
    host: Host,
    config: Config,
    db: Database,
    scanner: Scanner,
    current_session: Callable,
    deployer: Deployer,
    projects: Projects,
) -> None:
    def find_project(project_id: str) -> dict:
        found = db.get_project(project_id)
        if found is None:
            raise HTTPException(404, f"unknown project {project_id}")
        return found

    def check_source(source: SourceBody) -> None:
        if not REPO_RE.fullmatch(source.repo) or ".." in source.repo or not BRANCH_RE.fullmatch(source.branch):
            raise HTTPException(400, "invalid repository or branch")

    def plan(manifest: dict, project_id: str | None) -> list[dict]:
        """Every app of the file: is it in Mas already, and can it join this project?"""
        apps = {a["id"]: a for a in db.list_apps()}
        out = []
        for entry in manifest["apps"]:
            current = apps.get(entry["id"])
            problem = None
            if current is not None:
                if current["mode"] == "stack" or current["repo"].lower() != entry["repo"].lower():
                    problem = f"the app {entry['id']} exists with another repository: rename it in the file"
                elif current.get("project") and current["project"] != project_id:
                    problem = f"the app {entry['id']} belongs to the project {current['project']}"
            out.append({**entry, "exists": current is not None, "problem": problem,
                        "app": app_payload(current, db) if current is not None else None})
        return out

    def payload(project: dict) -> dict:
        manifest = json.loads(project["manifest"])
        return {"id": project["id"], "name": project["name"], "manifest": manifest,
                "source": json.loads(project["source"]) if project["source"] else None,
                "apps": plan(manifest, project["id"]), "order": deploy_order(manifest),
                "file": dump_manifest(manifest), "updated_at": project["updated_at"]}

    def apply(body: SaveProject, actor: str, project_id: str | None = None) -> dict:
        """Record the project, create the apps it lacks, link them, then deploy them in order."""
        try:
            manifest = parse_manifest(body.manifest)
        except ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        if project_id is not None:
            manifest["id"] = project_id
        pid = manifest["id"]
        if project_id is None and db.get_project(pid) is not None and not body.source:
            raise HTTPException(409, f"project {pid} already exists")
        problems = [p["problem"] for p in plan(manifest, pid) if p["problem"]]
        if problems:
            raise HTTPException(409, problems[0])
        existing = db.get_project(pid)
        source = body.source.model_dump() if body.source else (
            json.loads(existing["source"]) if existing and existing["source"] else None)
        missing = [a for a in manifest["apps"] if db.get_app(a["id"]) is None]
        if missing and not body.create_missing:
            missing = []
        gh = projects.github() if missing else None
        if missing and gh is None:
            raise HTTPException(409, "GitHub App not configured (Settings → GitHub)")
        resolved: list[dict] = []
        for entry in missing:  # everything is checked on GitHub before anything is created
            try:
                if source and source["repo"].lower() == entry["repo"].lower():
                    installation = source["installation_id"]
                else:
                    installation = gh.installation_for(entry["repo"])
                branch = entry["branch"] or gh.default_branch(installation, entry["repo"])
                app_type = ServiceType(entry["type"]) if entry["type"] else gh.detect_type(
                    installation, entry["repo"], branch)
            except GitHubError as exc:
                log.warning("project %s: %s: %s", pid, entry["repo"], exc)
                raise HTTPException(400, f"cannot read {entry['repo']}: install the GitHub App on it "
                                         "(Settings → GitHub)") from None
            if app_type == ServiceType.UNKNOWN:
                raise HTTPException(400, f"the type of {entry['id']} is not recognised: add \"type\" to "
                                         f"{MANIFEST_FILE}")
            resolved.append({**entry, "installation_id": installation, "branch": branch, "app_type": app_type})
        db.save_project(pid, manifest["name"], json.dumps(manifest), json.dumps(source) if source else None)
        created = []
        for entry in resolved:
            try:
                register_managed_app(host, config, db, app_id=entry["id"], repo=entry["repo"],
                                     branch=entry["branch"], installation_id=entry["installation_id"],
                                     domain=entry["domain"], app_type=entry["app_type"],
                                     build_command=entry["build_command"], start_command=entry["start_command"],
                                     root_dir=entry["root_dir"], project=pid)
            except (DeployError, EnvError) as exc:
                db.audit(actor, "project-save", False, detail=f"{pid}: {entry['id']}: {exc}"[:500])
                raise HTTPException(400, f"{entry['id']}: {exc}") from None
            created.append(entry["id"])
        ids = {a["id"] for a in manifest["apps"]}
        for current in db.list_apps():
            if current["id"] in ids and current.get("project") != pid:
                db.update_app(current["id"], project=pid)
            elif current["id"] not in ids and current.get("project") == pid:
                db.update_app(current["id"], project=None)  # left the file: still an app, outside the project
        queued = []
        if body.deploy or created:
            for app_id in deploy_order(manifest):
                if db.get_app(app_id) is None or (not body.deploy and app_id not in created):
                    continue
                trigger = "create" if app_id in created else "manual"
                queued.append({"app_id": app_id, "release_id": deployer.enqueue(app_id, trigger, actor)})
        db.audit(actor, "project-save", True, detail=f"{pid}: {', '.join(sorted(ids))}"
                 + (f"; created {', '.join(created)}" if created else ""))
        return payload(db.get_project(pid)) | {"created": created, "queued": queued}

    # -- projects ----------------------------------------------------------------

    @app.get("/api/projects")
    def list_projects(_=Depends(current_session)) -> list[dict]:
        return [payload(p) for p in db.list_projects()]

    @app.post("/api/projects/read")
    async def read_project(body: SourceBody, _=Depends(current_session)) -> dict:
        """The mas.project.json of a repository and what importing it would do."""
        check_source(body)
        try:
            manifest = await asyncio.to_thread(projects.read, body.installation_id, body.repo, body.branch)
        except ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        current = db.get_project(manifest["id"])
        return {"manifest": manifest, "apps": plan(manifest, manifest["id"]), "order": deploy_order(manifest),
                "file": dump_manifest(manifest), "exists": current is not None}

    @app.post("/api/projects/validate")
    def validate_project(body: SaveProject, _=Depends(current_session)) -> dict:
        """Check a project composed in the dashboard, and show its mas.project.json."""
        try:
            manifest = parse_manifest(body.manifest)
        except ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"manifest": manifest, "apps": plan(manifest, manifest["id"]), "order": deploy_order(manifest),
                "file": dump_manifest(manifest)}

    @app.post("/api/projects")
    async def save_project(body: SaveProject, s=Depends(current_session)) -> dict:
        if body.source:
            check_source(body.source)
        return await asyncio.to_thread(apply, body, s.username)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, _=Depends(current_session)) -> dict:
        return payload(find_project(project_id))

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, body: SaveProject, s=Depends(current_session)) -> dict:
        find_project(project_id)
        if body.source:
            check_source(body.source)
        return await asyncio.to_thread(apply, body, s.username, project_id)

    @app.get("/api/projects/{project_id}/file", response_class=PlainTextResponse)
    def project_file(project_id: str, _=Depends(current_session)) -> str:
        return dump_manifest(json.loads(find_project(project_id)["manifest"]))

    @app.post("/api/projects/{project_id}/sync")
    async def sync_project(project_id: str, s=Depends(current_session)) -> dict:
        """Read mas.project.json again from its repository (new apps are created, not deployed)."""
        project = find_project(project_id)
        if not project["source"]:
            raise HTTPException(400, "this project was composed in Mas: export it to a repository first")
        source = json.loads(project["source"])
        try:
            manifest = await asyncio.to_thread(projects.read, source["installation_id"], source["repo"],
                                               source["branch"])
        except ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        body = SaveProject(manifest=manifest, source=SourceBody(**source), deploy=False)
        return await asyncio.to_thread(apply, body, s.username, project_id)

    @app.post("/api/projects/{project_id}/deploy")
    def deploy_project(project_id: str, body: DeployProject, s=Depends(current_session)) -> dict:
        """Deploy every app of the project, each after the apps it depends on."""
        manifest = json.loads(find_project(project_id)["manifest"])
        queued = []
        for app_id in deploy_order(manifest):
            target = db.get_app(app_id)
            if target is None or (not body.include_frozen and not target["auto_deploy"]):
                continue
            queued.append({"app_id": app_id, "release_id": deployer.enqueue(app_id, "manual", s.username)})
        db.audit(s.username, "project-deploy", True,
                 detail=f"{project_id}: {', '.join(q['app_id'] for q in queued) or 'nothing'}")
        return {"queued": queued}

    @app.post("/api/projects/{project_id}/export")
    async def export_project(project_id: str, body: SourceBody, s=Depends(current_session)) -> dict:
        """Commit mas.project.json to a repository; the project then follows that file."""
        find_project(project_id)
        check_source(body)
        try:
            sha = await asyncio.to_thread(projects.export, project_id, body.installation_id, body.repo,
                                          body.branch, s.username)
        except ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"sha": sha} | payload(db.get_project(project_id))

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, s=Depends(current_session)) -> dict:
        """Forget the project; its apps stay as they are."""
        find_project(project_id)
        db.delete_project(project_id)
        db.audit(s.username, "project-delete", True, detail=project_id)
        return {"ok": True}

    # -- folders -----------------------------------------------------------------

    def find_folder(folder_id: int) -> dict:
        found = db.get_folder(folder_id)
        if found is None:
            raise HTTPException(404, "unknown folder")
        return found

    def ancestors(folder_id: int | None) -> list[int]:
        chain, seen = [], set()
        while folder_id is not None and folder_id not in seen:
            seen.add(folder_id)
            chain.append(folder_id)
            folder = db.get_folder(folder_id)
            folder_id = folder["parent_id"] if folder else None
        return chain

    def check_color(color: str | None) -> None:
        if color is not None and color not in FOLDER_COLORS:
            raise HTTPException(400, f"color: one of {', '.join(FOLDER_COLORS)}")

    def check_parent(parent_id: int | None, moving: int | None = None) -> None:
        if parent_id is None:
            return
        find_folder(parent_id)
        chain = ancestors(parent_id)
        if moving is not None and moving in chain:
            raise HTTPException(400, "a folder cannot go inside itself")
        if len(chain) >= MAX_DEPTH:
            raise HTTPException(400, f"folders go {MAX_DEPTH} levels deep at most")

    @app.get("/api/folders")
    def list_folders(_=Depends(current_session)) -> dict:
        return {"folders": db.list_folders(), "items": db.folder_items(), "colors": list(FOLDER_COLORS)}

    @app.post("/api/folders")
    def create_folder(body: FolderBody, s=Depends(current_session)) -> dict:
        check_color(body.color)
        check_parent(body.parent_id)
        if len(db.list_folders()) >= MAX_FOLDERS:
            raise HTTPException(400, f"{MAX_FOLDERS} folders at most")
        folder_id = db.create_folder(body.name.strip(), body.parent_id, body.color)
        db.audit(s.username, "folder-create", True, detail=body.name.strip())
        return find_folder(folder_id)

    @app.patch("/api/folders/{folder_id}")
    def patch_folder(folder_id: int, body: PatchFolder, s=Depends(current_session)) -> dict:
        find_folder(folder_id)
        fields = {k: getattr(body, k) for k in body.model_fields_set}
        if "name" in fields:
            if not (fields["name"] or "").strip():
                raise HTTPException(400, "the folder needs a name")
            fields["name"] = fields["name"].strip()
        if "color" in fields:
            check_color(fields["color"])
        if "parent_id" in fields:
            check_parent(fields["parent_id"], moving=folder_id)
        if "position" in fields and fields["position"] is None:
            fields.pop("position")
        if fields:
            db.update_folder(folder_id, **fields)
            db.audit(s.username, "folder-edit", True, detail=f"{folder_id}: {', '.join(fields)}")
        return find_folder(folder_id)

    @app.delete("/api/folders/{folder_id}")
    def delete_folder(folder_id: int, s=Depends(current_session)) -> dict:
        """Remove a folder; what it holds moves up one level (nothing is deleted)."""
        folder = find_folder(folder_id)
        db.delete_folder(folder_id)
        db.audit(s.username, "folder-delete", True, detail=folder["name"])
        return {"ok": True}

    @app.put("/api/folders/items")
    def move_items(body: MoveItems, s=Depends(current_session)) -> dict:
        """Put apps and services in a folder (``folder_id`` null: out of every folder)."""
        if body.folder_id is not None:
            find_folder(body.folder_id)
        for item in body.items:
            if item.kind not in FOLDER_KINDS:
                raise HTTPException(400, f"kind: one of {', '.join(FOLDER_KINDS)}")
            db.set_folder_item(item.kind, item.id, body.folder_id)
        return {"ok": True, "moved": len(body.items)}

    @app.post("/api/folders/{folder_id}/sort")
    def sort_folder(folder_id: int, body: SortFolder, s=Depends(current_session)) -> dict:
        """Sort what is directly in a folder into one sub-folder per type (existing ones are reused)."""
        find_folder(folder_id)
        children = {f["name"].lower(): f["id"] for f in db.list_folders() if f["parent_id"] == folder_id}
        apps = {a["id"]: a for a in db.list_apps()}
        moved = 0
        for item in db.folder_items():
            if item["folder_id"] != folder_id:
                continue
            if item["kind"] == "app":
                found = apps.get(item["item_id"])
                kind = None if found is None else "stack" if found["mode"] == "stack" else found["type"]
            else:
                svc = scanner.get(item["item_id"])
                kind = svc.type.value if svc else None
            if kind is None:
                continue
            name = (body.labels.get(kind) or kind).strip()[:60] or kind
            if name.lower() not in children:
                check_parent(folder_id)
                children[name.lower()] = db.create_folder(name, folder_id, None)
            db.set_folder_item(item["kind"], item["item_id"], children[name.lower()])
            moved += 1
        db.audit(s.username, "folder-sort", True, detail=f"{folder_id}: {moved} item(s) by type")
        return {"moved": moved}
