"""Public data model shared by the CLI, the API and the dashboard."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ServiceType(str, Enum):
    PHP = "php"
    NEXTJS = "nextjs"
    NODE = "node"
    PYTHON = "python"
    STATIC = "static"
    UNKNOWN = "unknown"


class Manager(str, Enum):
    NGINX_STATIC = "nginx-static"
    PHP_FPM = "php-fpm"
    SYSTEMD = "systemd"
    PM2 = "pm2"
    DOCKER = "docker"
    UNKNOWN = "unknown"


class Status(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERRORED = "errored"
    UNKNOWN = "unknown"


class GitCommit(BaseModel):
    sha: str
    subject: str | None = None
    author: str | None = None
    date: datetime | None = None


class Service(BaseModel):
    id: str
    name: str
    domains: list[str] = Field(default_factory=list)
    type: ServiceType = ServiceType.UNKNOWN
    type_source: str = "detected"  # "detected" | "manual"
    manager: Manager = Manager.UNKNOWN
    path: str | None = None
    port: int | None = None
    pid: int | None = None
    user: str | None = None
    status: Status = Status.UNKNOWN
    ssl_expires: datetime | None = None
    git_remote: str | None = None
    git_branch: str | None = None
    last_commit: GitCommit | None = None

    # Extra context, useful for actions (phase 2) and debugging.
    unit: str | None = None  # systemd unit, pm2 app name or container name
    container_id: str | None = None
    upstream: str | None = None  # raw proxy_pass / fastcgi_pass target
    routes: list[str] = Field(default_factory=list)  # "domain/path" served by this backend
    nginx_files: list[str] = Field(default_factory=list)
    ssl_certificate: str | None = None
    https_redirect: bool = False
    cmdline: str | None = None

    @property
    def has_domain(self) -> bool:
        return bool(self.domains)


class ScanResult(BaseModel):
    scanned_at: datetime
    services: list[Service]
    warnings: list[str] = Field(default_factory=list)
