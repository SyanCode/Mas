"""Preview deployments: a pull request gets its own copy of the app, at ``pr<N>.<domain>``.

Opt-in per app. A preview is an ordinary managed app (``environment = "preview"``) that follows the
pull request's branch: pushes to the branch redeploy it through the normal webhook path, closing or
merging the pull request removes it with its files. Only pull requests from the same repository get one
(a fork's code never runs on the server), the variables of the app are not copied unless the owner
asked for it, and at most ``max`` previews live at the same time per app. The domain needs a wildcard
DNS record (``*.<domain>``) pointing at the server.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from typing import Callable

from mas.db import Database
from mas.deploy import DeployError, Deployer
from mas.github import GitHubError
from mas.models import ServiceType

log = logging.getLogger(__name__)

OPENING = {"opened", "reopened", "ready_for_review"}
DEFAULTS = {"enabled": False, "env": False, "max": 5, "allow": []}
MAX_ALLOW = 30
SWEEP_EVERY = 30 * 60  # seconds between two looks at the pull requests


def normalize_networks(entries: list[str]) -> list[str]:
    """``1.2.3.4`` / ``1.2.3.0/24`` / ``2a01::/64`` → canonical networks, deduplicated; never the whole internet."""
    out: list[str] = []
    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise DeployError(f"not an IP address or network: {text}") from None
        if net.prefixlen == 0:
            raise DeployError(f"{text} would open the previews to the whole internet")
        value = str(net.network_address) if net.num_addresses == 1 else str(net)
        if value not in out:
            out.append(value)
    if len(out) > MAX_ALLOW:
        raise DeployError(f"at most {MAX_ALLOW} addresses")
    return out


def preview_id(parent: str, number: int) -> str:
    suffix = f"-pr{number}"
    return f"{parent[:24 - len(suffix)].rstrip('-')}{suffix}"


def preview_domain(parent_domain: str, number: int) -> str:
    return f"pr{number}.{parent_domain.removeprefix('www.')}"


class Previews:
    def __init__(self, db: Database, deployer: Deployer, register: Callable[..., None],
                 github_client: Callable[[], object | None]) -> None:
        self.db, self.deployer, self.register, self.github_client = db, deployer, register, github_client

    # -- settings ---------------------------------------------------------------------------------------

    def settings(self, app_id: str) -> dict:
        raw = json.loads(self.db.get_setting(f"previews:{app_id}") or "null") or {}
        return {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}

    def configure(self, app_id: str, body: dict) -> dict:
        count = body.get("max", 5)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 20:
            raise DeployError("max: 1 to 20 previews")
        allow = body.get("allow", self.settings(app_id)["allow"])
        if not isinstance(allow, list):
            raise DeployError("allow: a list of addresses")
        networks = normalize_networks(allow)
        changed = networks != self.settings(app_id)["allow"]
        self.db.set_setting(f"previews:{app_id}", json.dumps(
            {"enabled": bool(body.get("enabled")), "env": bool(body.get("env")), "max": count, "allow": networks}))
        if changed:  # the previews already live follow the new list right away
            for p in self.of_parent(app_id):
                try:
                    self.deployer.refresh_vhost(p["id"])
                except DeployError as exc:
                    log.warning("preview %s: %s", p["id"], exc)
        return self.settings(app_id)

    # -- what exists ---------------------------------------------------------------------------------------

    def meta(self, app_id: str) -> dict | None:
        return json.loads(self.db.get_setting(f"preview:{app_id}") or "null")

    def of_parent(self, parent: str) -> list[dict]:
        found = []
        for a in self.db.list_apps():
            m = self.meta(a["id"]) if a.get("environment") == "preview" else None
            if m and m["parent"] == parent:
                found.append({"id": a["id"], "pr": m["pr"], "domain": a["domain"], "branch": a["branch"],
                              "status": (self.db.list_releases(a["id"], 1) or [{}])[0].get("status")})
        return sorted(found, key=lambda p: p["pr"])

    # -- the pull_request webhook ------------------------------------------------------------------------------

    def handle(self, payload: dict, actor: str) -> dict:
        action = payload.get("action", "")
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name", "")
        number = pr.get("number")
        if not repo or not isinstance(number, int):
            return {"ok": True, "ignored": "pull_request"}
        if action == "closed":
            return {"ok": True, "removed": self.close(repo, number)}
        if action not in OPENING and action != "synchronize":
            return {"ok": True, "ignored": f"pull_request {action}"}
        head = pr.get("head") or {}
        if (head.get("repo") or {}).get("full_name", "").lower() != repo.lower():
            return {"ok": True, "ignored": "pull request from a fork"}  # its code never runs here
        if pr.get("draft"):
            return {"ok": True, "ignored": "draft"}
        branch, sha, base = head.get("ref", ""), head.get("sha", ""), (pr.get("base") or {}).get("ref", "")
        out = []
        for parent in self.db.apps_for_push(repo, base, frozen=True):
            if parent["mode"] != "managed" or parent.get("environment") == "preview" or not parent.get("domain"):
                continue
            if not self.settings(parent["id"])["enabled"]:
                continue
            made = self.ensure(parent, number, branch, sha, action, actor, pr)
            if made:
                out.append(made)
        return {"ok": True, "previews": out}

    def ensure(self, parent: dict, number: int, branch: str, sha: str, action: str, actor: str, pr: dict) -> str | None:
        settings = self.settings(parent["id"])
        pid = preview_id(parent["id"], number)
        existing = self.db.get_app(pid)
        if existing:
            meta = self.meta(pid)
            if existing.get("environment") != "preview" or not meta or meta["parent"] != parent["id"]:
                self.db.audit(f"github:{actor}", "preview-create", False, service_id=parent["id"],
                              detail=f"PR #{number}: the id {pid} is taken by another app")
                return None  # never deploy over an app that is not this preview
            if action in OPENING:  # a reopened pull request: the push events cover the later commits
                try:
                    self.deployer.enqueue(pid, "push", f"github:{actor}", sha or None)
                except DeployError as exc:
                    log.warning("preview %s: %s", pid, exc)
            return pid
        if len(self.of_parent(parent["id"])) >= settings["max"]:
            self.db.audit(f"github:{actor}", "preview-skipped", False, service_id=parent["id"],
                          detail=f"PR #{number}: {settings['max']} previews already live")
            return None
        domain = preview_domain(parent["domain"], number)
        try:
            self.register(app_id=pid, repo=parent["repo"], branch=branch, installation_id=parent["installation_id"],
                          domain=domain, app_type=ServiceType(parent["type"]), env="",
                          build_command=parent.get("build_command"), start_command=parent.get("start_command"),
                          root_dir=parent.get("root_dir") or "", auto_deploy=True, aliases=[])
        except (DeployError, ValueError) as exc:
            self.db.audit(f"github:{actor}", "preview-create", False, service_id=parent["id"], detail=f"PR #{number}: {exc}")
            return None
        fields = {"environment": "preview", "name": f"{parent.get('name') or parent['id']} · PR #{number}"}
        if settings["env"] and parent.get("env"):
            fields["env"] = parent["env"]  # same key, same encrypted token: asked for by the owner
        self.db.update_app(pid, **fields)
        self.db.set_setting(f"preview:{pid}", json.dumps({"parent": parent["id"], "pr": number, "repo": parent["repo"]}))
        self.db.audit(f"github:{actor}", "preview-create", True, service_id=pid, detail=f"PR #{number} → {domain}")
        try:
            self.deployer.enqueue(pid, "create", f"github:{actor}", sha or None)
        except DeployError as exc:
            log.warning("preview %s: %s", pid, exc)
        self._comment(parent, number, f"Preview of this pull request: https://{domain} (deploying, it updates at every push; "
                      "removed when the pull request is closed).")
        return pid

    def sweep(self, now: float | None = None, force: bool = False) -> list[str]:
        """Remove the previews whose pull request is closed (or gone) but whose webhook was missed, for example
        while Mas was stopped. Looked at most every 30 minutes; GitHub being unreachable removes nothing."""
        now = now or time.time()
        if not force and now - float(self.db.get_setting("previews:swept") or 0) < SWEEP_EVERY:
            return []
        self.db.set_setting("previews:swept", str(now))
        gh = self.github_client()
        removed = []
        for a in self.db.list_apps():
            meta = self.meta(a["id"]) if a.get("environment") == "preview" else None
            if not meta:
                continue
            parent = self.db.get_app(meta["parent"])
            if parent is None:  # its app is gone: nothing keeps this copy alive
                self.remove(a["id"])
                removed.append(a["id"])
                continue
            if gh is None or not parent.get("installation_id"):
                continue
            try:
                state = gh.pull_state(parent["installation_id"], meta["repo"], meta["pr"])
            except (GitHubError, OSError) as exc:
                log.info("preview %s: cannot ask GitHub (%s)", a["id"], exc)
                continue
            if state in ("closed", "gone"):
                self.remove(a["id"])
                removed.append(a["id"])
        return removed

    def close(self, repo: str, number: int) -> list[str]:
        removed = []
        for a in self.db.list_apps():
            m = self.meta(a["id"]) if a.get("environment") == "preview" else None
            if m and m["pr"] == number and m["repo"].lower() == repo.lower():
                self.remove(a["id"])
                removed.append(a["id"])
        return removed

    def remove(self, pid: str) -> None:
        try:
            self.deployer.remove_app(pid, purge_files=True)
        except DeployError as exc:
            log.warning("preview %s not removed: %s", pid, exc)
            return
        self.db.set_setting(f"preview:{pid}", None)
        self.db.audit("previews", "preview-remove", True, service_id=pid)

    def remove_all(self, parent: str) -> list[str]:
        gone = [p["id"] for p in self.of_parent(parent)]
        for pid in gone:
            self.remove(pid)
        return gone

    def _comment(self, parent: dict, number: int, text: str) -> None:
        gh = self.github_client()
        if gh is None or not parent.get("installation_id"):
            return
        try:
            gh.comment(parent["installation_id"], parent["repo"], number, text)
        except (GitHubError, OSError) as exc:  # the App may not have the "pull requests: write" permission
            log.info("no comment on %s#%s: %s", parent["repo"], number, exc)
