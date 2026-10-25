"""Mas as an agent: a server of the cluster without a dashboard, driven by the primary server.

It listens on its private address only and answers the calls of the primary (join token): its
information, and the copies of apps to build, check and remove. No scan, no background job: only
the deploy worker, so it costs almost nothing next to the apps it runs.
"""

from __future__ import annotations

from fastapi import FastAPI

from mas import __version__, api_cluster
from mas.cluster import Cluster
from mas.config import Config
from mas.db import Database
from mas.deploy import Deployer
from mas.host import Host


def create_agent_app(host: Host, config: Config, db: Database, cluster_opener=None) -> FastAPI:
    deployer = Deployer(host, config, db, lambda: None)  # copies get the commit and a clone token from the primary
    cluster = Cluster(host, config, db, deployer, opener=cluster_opener) if cluster_opener else \
        Cluster(host, config, db, deployer)
    if config.cluster_address and cluster.address() != config.cluster_address:
        cluster.configure(None, config.cluster_address)
    app = FastAPI(title="Mas agent", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "role": "agent"}

    api_cluster.register(app, db=db, current_session=None, cluster=cluster, deployer=deployer)
    app.state.deployer, app.state.cluster = deployer, cluster
    return app
