"""Docker containers through the Engine API socket (optional)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.collectors import ScanContext
from mas.host import DOCKER_SOCKET

DOCKER_STATUS = {"running": "running", "exited": "stopped", "created": "stopped",
                 "paused": "stopped", "restarting": "errored", "dead": "errored"}

COMPOSE_WORKDIR = "com.docker.compose.project.working_dir"


@dataclass
class PublishedPort:
    private: int
    public: int | None
    ip: str | None
    proto: str


@dataclass
class Container:
    id: str
    name: str
    image: str
    state: str
    status: str  # running | stopped | errored | unknown
    ports: list[PublishedPort] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def public_ports(self) -> list[int]:
        return sorted({p.public for p in self.ports if p.public and p.proto == "tcp"})

    @property
    def working_dir(self) -> str | None:
        return self.labels.get(COMPOSE_WORKDIR)


def parse_containers(data: list[dict]) -> list[Container]:
    out = []
    for c in data:
        names = c.get("Names") or []
        state = c.get("State", "")
        out.append(
            Container(
                id=c["Id"],
                name=names[0].lstrip("/") if names else c["Id"][:12],
                image=c.get("Image", ""),
                state=state,
                status=DOCKER_STATUS.get(state, "unknown"),
                ports=[
                    PublishedPort(p.get("PrivatePort"), p.get("PublicPort"), p.get("IP"), p.get("Type", "tcp"))
                    for p in c.get("Ports") or []
                ],
                labels=dict(c.get("Labels") or {}),
            )
        )
    return out


def collect(ctx: ScanContext) -> list[Container]:
    if not ctx.host.exists(DOCKER_SOCKET):
        ctx.warn("docker: no socket at /var/run/docker.sock, Docker skipped")
        return []
    try:
        data = ctx.host.docker_get("/containers/json?all=1")
    except (OSError, ValueError) as exc:
        ctx.warn(f"docker: API unavailable ({exc})")
        return []
    try:
        return parse_containers(data)
    except (KeyError, TypeError) as exc:
        ctx.warn(f"docker: unexpected API response ({exc})")
        return []
