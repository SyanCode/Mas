"""API routes of the cluster: the Servers page (administrators) and the calls between servers (join token)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from mas.cluster import Cluster, ClusterError
from mas.db import Database
from mas.deploy import DeployError, Deployer, validate_app, validate_commands


class SelfBody(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    address: str | None = Field(default=None, max_length=64)


class TokenBody(BaseModel):
    name: str = Field(default="cluster", max_length=60)


class ServerBody(BaseModel):
    url: str = Field(max_length=300)
    token: str = Field(min_length=10, max_length=200)


class AppServersBody(BaseModel):
    servers: list[int] = Field(default_factory=list, max_length=20)


class CopySpec(BaseModel):
    name: str = Field(max_length=100)
    repo: str = Field(max_length=200)
    branch: str = Field(max_length=200)
    type: str = Field(max_length=20)
    build_command: str | None = Field(default=None, max_length=1000)
    start_command: str | None = Field(default=None, max_length=1000)
    root_dir: str | None = Field(default="", max_length=200)
    env: dict[str, str] = Field(default_factory=dict)


class CopyBody(BaseModel):
    leader: str = Field(max_length=60)
    spec: CopySpec
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    git_token: str = Field(min_length=10, max_length=400)
    message: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=200)


def register(app: FastAPI, *, db: Database, current_session: Callable, cluster: Cluster, deployer: Deployer) -> None:

    def call(fn, *args):
        try:
            return fn(*args)
        except ClusterError as exc:
            raise HTTPException(400, str(exc)) from None

    def peer(request: Request) -> dict:
        """Another Mas holding a join token of this server."""
        auth = request.headers.get("authorization", "")
        found = cluster.check_token(auth[7:].strip() if auth.lower().startswith("bearer ") else None)
        if found is None:
            raise HTTPException(401, "invalid cluster token")
        return found

    # -- the Servers page ---------------------------------------------------------

    @app.get("/api/cluster")
    async def overview(_=Depends(current_session)) -> dict:
        return await asyncio.to_thread(cluster.overview)

    @app.get("/api/cluster/traffic")
    async def traffic(_=Depends(current_session)) -> dict:
        """Requests per app and per backend, for the cluster map (refreshed every few seconds)."""
        return await asyncio.to_thread(cluster.traffic)

    @app.put("/api/cluster/self")
    def configure(body: SelfBody, s=Depends(current_session)) -> dict:
        call(cluster.configure, body.name, body.address)
        db.audit(s.username, "cluster-self", True, detail=f"{cluster.name()} · {cluster.address() or 'no address'}")
        return {"name": cluster.name(), "address": cluster.address()}

    @app.post("/api/cluster/tokens")
    def create_token(body: TokenBody, s=Depends(current_session)) -> dict:
        token = cluster.create_token(body.name)
        db.audit(s.username, "cluster-token", True, detail=body.name)
        return {"token": token}

    @app.delete("/api/cluster/tokens/{token_id}")
    def delete_token(token_id: int, s=Depends(current_session)) -> dict:
        db.delete_cluster_token(token_id)
        db.audit(s.username, "cluster-token-delete", True, detail=str(token_id))
        return {"ok": True}

    @app.post("/api/cluster/servers")
    async def add_server(body: ServerBody, s=Depends(current_session)) -> dict:
        added = await asyncio.to_thread(call, cluster.add_server, body.url, body.token)
        db.audit(s.username, "cluster-join", True, detail=f"{added['name']} ({body.url})")
        return added

    @app.delete("/api/cluster/servers/{server_id}")
    async def remove_server(server_id: int, s=Depends(current_session)) -> dict:
        notes = await asyncio.to_thread(call, cluster.remove_server, server_id)
        db.audit(s.username, "cluster-leave", True, detail="; ".join(notes)[:500] or str(server_id))
        return {"notes": notes}

    @app.put("/api/apps/{app_id}/cluster")
    async def app_servers(app_id: str, body: AppServersBody, deploy: bool = True, s=Depends(current_session)) -> dict:
        target = db.get_app(app_id)
        if target is None:
            raise HTTPException(404, "unknown app")
        notes = await asyncio.to_thread(call, cluster.set_app_servers, target, body.servers)
        db.audit(s.username, "app-cluster", True, detail=f"{app_id}: servers {body.servers or 'none'}")
        rid = deployer.enqueue(app_id, "manual", s.username) if deploy else None
        return {"notes": notes, "release_id": rid}

    # -- calls from the Mas that drives this server -------------------------------------

    @app.get("/api/cluster/self")
    def self_info(_=Depends(peer)) -> dict:
        return cluster.info()

    @app.put("/api/cluster/copies/{app_id}")
    def put_copy(app_id: str, body: CopyBody, who=Depends(peer)) -> dict:
        try:
            validate_app(app_id, body.spec.repo, body.spec.branch, None, body.spec.type)
            validate_commands(body.spec.build_command, body.spec.start_command, (body.spec.root_dir or "").strip("/"))
        except DeployError as exc:
            raise HTTPException(400, str(exc)) from None
        started = call(cluster.put_copy, app_id, body.model_dump())
        db.audit(f"cluster:{who['name']}", "cluster-copy", True, service_id=app_id,
                 detail=f"{body.spec.repo}@{body.sha[:7]} for {body.leader}")
        return started

    @app.get("/api/cluster/copies/{app_id}/releases/{release_id}")
    def copy_release(app_id: str, release_id: int, _=Depends(peer)) -> dict:
        return call(cluster.copy_release, app_id, release_id)

    @app.delete("/api/cluster/copies/{app_id}")
    def delete_copy(app_id: str, who=Depends(peer)) -> dict:
        done = call(cluster.delete_copy, app_id)
        db.audit(f"cluster:{who['name']}", "cluster-copy-delete", True, service_id=app_id, detail="; ".join(done)[:500])
        return {"done": done}
