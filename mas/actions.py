"""Allowed actions on detected services (strict allow-list).

Every ``(manager, action)`` pair maps to one precise command built as an
argument list. Identifiers (unit, app, container, user) come from the scan,
never from the request, and are validated again before use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mas.collectors.pm2 import pm2_command
from mas.collectors.processes import load_users
from mas.host import Host
from mas.models import Manager, Service

ACTIONS = ("start", "stop", "restart", "reload")
CONFIRM_ACTIONS = {"stop", "restart"}  # the UI and CLI ask before these

ALLOWED: dict[Manager, tuple[str, ...]] = {
    Manager.SYSTEMD: ("start", "stop", "restart", "reload"),
    Manager.PM2: ("start", "stop", "restart", "reload"),
    Manager.DOCKER: ("start", "stop", "restart"),
    Manager.PHP_FPM: ("reload", "restart"),
    Manager.NGINX_STATIC: ("reload",),
}

UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]+\.service$")
PM2_NAME_RE = re.compile(r"^[A-Za-z0-9._@-]+$")
CONTAINER_RE = re.compile(r"^[0-9a-f]{12,64}$")
ACTION_TIMEOUT = 90.0


class ActionError(Exception):
    pass


@dataclass
class ActionResult:
    ok: bool
    action: str
    service_id: str | None
    command: list[str] = field(default_factory=list)
    output: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "action": self.action, "service_id": self.service_id,
                "command": self.command, "output": self.output}


def available_actions(svc: Service) -> list[str]:
    allowed = ALLOWED.get(svc.manager, ())
    if svc.manager == Manager.PHP_FPM and not svc.unit:
        return []
    if svc.manager in (Manager.SYSTEMD, Manager.PM2, Manager.DOCKER) and not (svc.unit or svc.container_id):
        return []
    return list(allowed)


def _check(regex: re.Pattern, value: str | None, what: str) -> str:
    if not value or not regex.fullmatch(value):
        raise ActionError(f"refusing to act on invalid {what}: {value!r}")
    return value


def nginx_reload(host: Host) -> ActionResult:
    """``nginx -t`` first; reload only if the configuration is valid."""
    test = host.run(["nginx", "-t"], timeout=ACTION_TIMEOUT)
    if test is None:
        return ActionResult(False, "nginx-reload", None, ["nginx", "-t"], "nginx binary not found")
    test_output = (test.stderr + test.stdout).strip()
    if not test.ok:
        return ActionResult(False, "nginx-reload", None, ["nginx", "-t"],
                            f"configuration test failed, nginx NOT reloaded:\n{test_output}")
    argv = ["systemctl", "reload", "nginx"]
    res = host.run(argv, timeout=ACTION_TIMEOUT)
    if res is None:
        return ActionResult(False, "nginx-reload", None, argv, "systemctl not found")
    return ActionResult(res.ok, "nginx-reload", None, argv,
                        "\n".join(x for x in (test_output, res.stdout.strip(), res.stderr.strip()) if x))


def _command(host: Host, svc: Service, action: str) -> list[str]:
    if svc.manager in (Manager.SYSTEMD, Manager.PHP_FPM):
        return ["systemctl", action, _check(UNIT_RE, svc.unit, "systemd unit")]
    if svc.manager == Manager.PM2:
        name = _check(PM2_NAME_RE, svc.unit, "pm2 app name")
        user = next((u for u in load_users(host).values() if u.name == svc.user), None)
        if user is None:
            raise ActionError(f"unknown pm2 owner {svc.user!r}")
        argv = pm2_command(host, user, [action, name])
        if argv is None:
            raise ActionError("pm2 binary not found or insufficient privileges")
        return argv
    raise ActionError(f"no command for {svc.manager.value}")


def run_action(host: Host, svc: Service, action: str) -> ActionResult:
    if action not in available_actions(svc):
        raise ActionError(f"action {action!r} is not allowed for {svc.manager.value} service {svc.id}")

    if svc.manager == Manager.NGINX_STATIC:
        result = nginx_reload(host)
        result.service_id = svc.id
        return result

    if svc.manager == Manager.DOCKER:
        cid = _check(CONTAINER_RE, svc.container_id, "container id")
        api = f"/containers/{cid}/{action}"
        try:
            host.docker_request("POST", api)
        except OSError as exc:
            return ActionResult(False, action, svc.id, ["docker-api", "POST", api], str(exc))
        return ActionResult(True, action, svc.id, ["docker-api", "POST", api], "")

    argv = _command(host, svc, action)
    res = host.run(argv, timeout=ACTION_TIMEOUT)
    if res is None:
        return ActionResult(False, action, svc.id, argv, f"{argv[0]} not found or timed out")
    output = "\n".join(x for x in (res.stdout.strip(), res.stderr.strip()) if x)
    return ActionResult(res.ok, action, svc.id, argv, output)
