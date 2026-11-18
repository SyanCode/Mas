"""Automatic upgrade of the vulnerable dependencies found by mas.depaudit.

For an app linked to GitHub, Mas:

1. reads ``package.json`` / ``package-lock.json`` (or a pinned ``requirements.txt``) of the branch it deploys;
2. computes the new lock file in a throw-away folder, as the unprivileged user ``nobody``, with
   ``npm install|update --package-lock-only --ignore-scripts``: nothing is installed, no script of a package
   runs, only the registry is read;
3. checks that every vulnerable version is really gone, and commits the files (one commit, on top of the
   commit it read: refused if the branch moved meanwhile);
4. ``push`` mode: the commit goes on the deployed branch and is deployed. If that release fails (build or
   health check, the previous release keeps serving), Mas puts the files back as they were in a second
   commit, as long as nobody changed them since, and does not try that upgrade again.
   ``pr`` mode: the commit goes on a branch ``mas/deps-…`` with a pull request, a person merges it.

Only security fixes inside the same major version are made (a major upgrade can break the app: the advisor
and the robot propose it instead). Development packages are never touched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from mas.config import Config
from mas.db import Database
from mas.depaudit import DepAudit, parse_package_json, parse_package_lock, parse_requirements, version_key
from mas.host import Host

log = logging.getLogger(__name__)

MODES = ("off", "pr", "push")
SETTING = "deps:auto"
STATE = "deps:update:"  # + app id
DEFAULT_MODE = "push"
WEBHOOK_GRACE = 15 * 60  # no release for the commit after this: Mas deploys it itself
GIVE_UP = 3 * 3600  # still no result: the update is marked failed
NPM_TIMEOUT = 900
RETRY_AFTER = 7 * 86400  # an upgrade that failed is tried again after a week (a newer fix may exist)
NPM_FLAGS = ["--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"]

Runner = Callable[[list[str], str], tuple[int, str]]  # (argv, workdir) -> (exit code, output)


class UpdateError(Exception):
    pass


def _major(version: str) -> int:
    return version_key(version)[0][0]


def _now_iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


def _ts(value: str | None) -> float:
    try:
        return datetime.fromisoformat(value).timestamp() if value else 0.0
    except ValueError:
        return 0.0


def target_key(t: dict) -> str:
    return f"{t['ecosystem']}:{t['name']}@{t['to']}"


def plan(findings: list[dict], blocked: dict[str, float], now: float) -> tuple[list[dict], list[dict]]:
    """(what Mas can upgrade itself, what needs a person: no fix, a new major version, or an unknown tool)."""
    todo, manual = [], []
    for f in findings:
        t = {"ecosystem": f["ecosystem"], "name": f["name"], "version": f["version"], "direct": f["direct"],
             "to": f.get("upgrade_to")}
        if not t["to"]:
            manual.append(t | {"why": "no fixed version published", "code": "nofix"})
        elif f["ecosystem"] not in ("npm", "PyPI"):
            manual.append(t | {"why": f"{f['ecosystem']} packages are not upgraded automatically", "code": "ecosystem"})
        elif _major(t["to"]) != _major(t["version"]):
            manual.append(t | {"why": f"the fix is in a new major version ({t['to']})", "code": "major"})
        elif now - blocked.get(target_key(t), 0) < RETRY_AFTER:
            manual.append(t | {"why": "the automatic upgrade failed, not retried before a week", "code": "blocked"})
        elif not any(x["ecosystem"] == t["ecosystem"] and x["name"] == t["name"] for x in todo):
            todo.append(t)
    return todo, manual


def bump_requirements(text: str, targets: list[dict]) -> str:
    """name==old → name==new for each target, the rest of the line kept."""
    for t in targets:
        pattern = re.compile(r"(?im)^(\s*" + "[-_.]+".join(map(re.escape, re.split(r"[-_.]+", t["name"])))
                             + r"(?:\[[^\]]*\])?\s*===?\s*)" + re.escape(t["version"]) + r"(?=[\s;#,]|$)")
        text = pattern.sub(lambda m: m.group(1) + t["to"], text)
    return text


def _exact(spec: str | None) -> bool:
    return bool(spec) and bool(re.fullmatch(r"=?\d[\w.+-]*", spec.strip()))


PASS_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS",
            "SSL_CERT_FILE", "npm_config_registry")  # how this server reaches the registry, nothing else


def default_runner(tool_path: str) -> Runner:
    def run(argv: list[str], workdir: str) -> tuple[int, str]:
        passed = [f"{k}={os.environ[k]}" for k in PASS_ENV if os.environ.get(k)]
        env = ["env", "-i", f"HOME={workdir}/.home", f"npm_config_cache={workdir}/.npm", f"PATH={tool_path}",
               "CI=1", "LANG=C.UTF-8", *passed]
        try:
            proc = subprocess.run(["runuser", "-u", "nobody", "--", *env, *argv], capture_output=True, text=True,
                                  timeout=NPM_TIMEOUT, check=False, stdin=subprocess.DEVNULL, cwd=workdir)
        except FileNotFoundError:
            return 127, f"{argv[0]} not found"
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]} took more than {NPM_TIMEOUT // 60} minutes"
        return proc.returncode, (proc.stdout + proc.stderr)[-4000:]
    run.as_nobody = True  # type: ignore[attr-defined]
    return run


class DepUpdater:
    def __init__(self, db: Database, host: Host, config: Config, github: Callable, deployer, audit: DepAudit,
                 notifier=None, runner: Runner | None = None) -> None:
        self.db, self.host, self.config, self.github = db, host, config, github
        self.deployer, self.audit, self.notifier = deployer, audit, notifier
        self.runner = runner
        self._busy: set[str] = set()

    # -- settings and state ------------------------------------------------------------------------

    def mode(self) -> str:
        value = self.db.get_setting(SETTING)
        return value if value in MODES else DEFAULT_MODE

    def configure(self, mode: str) -> str:
        if mode not in MODES:
            raise UpdateError(f"mode: {', '.join(MODES)}")
        self.db.set_setting(SETTING, mode)
        return mode

    def state(self, app_id: str) -> dict:
        return json.loads(self.db.get_setting(STATE + app_id) or "null") or {}

    def _save(self, app_id: str, rec: dict) -> dict:
        self.db.set_setting(STATE + app_id, json.dumps(rec))
        return rec

    def _tell(self, app: dict, title: str, text: str, level: str = "info") -> None:
        self.db.audit("deps", "deps-update", level != "alert", service_id=app["id"], detail=f"{title}: {text}"[:400])
        if self.notifier is not None:
            try:
                self.notifier.notify(app["id"], "updates", title, text, level=level)
            except Exception:  # noqa: BLE001 - the update stands without its message
                log.warning("could not tell about the update of %s", app["id"], exc_info=True)

    # -- one update --------------------------------------------------------------------------------

    def _run(self) -> Runner:
        if self.runner is None:
            self.runner = default_runner(self.deployer._tool_path() if self.deployer else "/usr/local/bin:/usr/bin:/bin")
        return self.runner

    def _npm(self, files: dict[str, bytes], direct: list[dict], sub: list[dict]) -> tuple[dict[str, bytes], str]:
        """New package.json / package-lock.json, computed in a throw-away folder owned by nobody."""
        workdir = tempfile.mkdtemp(prefix="mas-deps-")
        try:
            for name, content in files.items():
                (Path(workdir) / name).write_bytes(content)
            for sub_dir in (".home", ".npm"):
                (Path(workdir) / sub_dir).mkdir()
            runner = self._run()
            if getattr(runner, "as_nobody", False):  # the real thing: hand the folder to nobody
                Path(workdir).chmod(0o755)
                res = subprocess.run(["chown", "-R", "nobody:", workdir], check=False, capture_output=True, text=True)
                if res.returncode != 0:
                    raise UpdateError(f"cannot hand the work folder to nobody: {res.stderr.strip()[:200]}")
            output = []
            spec = json.loads(files["package.json"] or b"{}")
            ranges = {**(spec.get("optionalDependencies") or {}), **(spec.get("dependencies") or {})}
            exact = [t for t in direct if _exact(ranges.get(t["name"]))]
            caret = [t for t in direct if t not in exact]
            steps = []
            if caret:
                steps.append(["npm", "install", *[f"{t['name']}@^{t['to']}" for t in caret], *NPM_FLAGS])
            if exact:
                steps.append(["npm", "install", "--save-exact", *[f"{t['name']}@{t['to']}" for t in exact], *NPM_FLAGS])
            if sub:
                steps.append(["npm", "update", *[t["name"] for t in sub], *NPM_FLAGS])
            for argv in steps:
                rc, out = runner(argv, workdir)
                output.append("$ " + " ".join(argv) + "\n" + out)
                if rc != 0:
                    raise UpdateError(f"`{' '.join(argv[:3])}` failed:\n{out[-1500:]}")
            return {n: (Path(workdir) / n).read_bytes() for n in files}, "\n".join(output)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def update(self, app: dict, now: float | None = None, trigger: str = "scheduled") -> dict:
        """Upgrade what can be upgraded now; returns the new state of the app."""
        now = now or time.time()
        mode = self.mode()
        if mode == "off":  # asked by hand while the automatic upgrades are off: a pull request, never a push
            mode = "pr"
        if app["id"] in self._busy:
            raise UpdateError("an update of this app is already running")
        rec = self.state(app["id"])
        if rec.get("state") in ("deploying",):
            raise UpdateError("the previous update is still being deployed")
        if not app.get("repo") or not app.get("installation_id"):
            raise UpdateError("this app is not linked to a GitHub repository")
        gh = self.github()
        if gh is None:
            raise UpdateError("GitHub is not connected (Settings → GitHub)")
        found = self.audit.latest(app["id"]) or {}
        blocked = {k: float(v) for k, v in (rec.get("blocked") or {}).items()}
        todo, manual = plan(found.get("findings") or [], blocked, now)
        base = {"blocked": blocked, "manual": manual, "at": _now_iso(now), "mode": mode}
        if not todo:
            return self._save(app["id"], {**rec, **base, "state": rec.get("state") if rec.get("state") == "pr" else "idle"})
        self._busy.add(app["id"])
        try:
            return self._update(app, gh, mode, todo, base, now)
        except Exception as exc:  # noqa: BLE001 - recorded, and these versions wait a week
            for t in todo:
                blocked[target_key(t)] = now
            rec = self._save(app["id"], {**base, "state": "failed", "error": str(exc)[:2000], "targets": todo})
            self._tell(app, "Automatic dependency upgrade failed", f"{', '.join(t['name'] for t in todo)}: {str(exc)[:500]}",
                       "alert")
            return rec
        finally:
            self._busy.discard(app["id"])

    def _update(self, app: dict, gh, mode: str, todo: list[dict], base: dict, now: float) -> dict:
        inst, repo, branch = app["installation_id"], app["repo"], app["branch"]
        head = gh.head(inst, repo, branch)
        changed: dict[str, bytes] = {}
        done: list[dict] = []
        failed: list[dict] = []
        npm = [t for t in todo if t["ecosystem"] == "npm"]
        if npm:
            pkg, _ = gh.read_file(inst, repo, "package.json", head)
            lock, _ = gh.read_file(inst, repo, "package-lock.json", head)
            if pkg is None or lock is None:
                raise UpdateError("package.json and package-lock.json must be at the root of the repository")
            new, _ = self._npm({"package.json": pkg, "package-lock.json": lock},
                               [t for t in npm if t["direct"]], [t for t in npm if not t["direct"]])
            installed = parse_package_lock(new["package-lock.json"].decode(), parse_package_json(new["package.json"].decode()))
            for t in npm:
                versions = [p["version"] for p in installed if p["name"] == t["name"]]
                ok = t["version"] not in versions and all(version_key(v) >= version_key(t["to"]) for v in versions)
                (done if ok else failed).append(t | {"now": max(versions, key=version_key) if versions else None})
            for name in ("package.json", "package-lock.json"):
                if new[name] != {"package.json": pkg, "package-lock.json": lock}[name]:
                    changed[name] = new[name]
        py = [t for t in todo if t["ecosystem"] == "PyPI"]
        if py:
            req, _ = gh.read_file(inst, repo, "requirements.txt", head)
            text = (req or b"").decode()
            new_text = bump_requirements(text, py)
            pinned = parse_requirements(new_text) if req is not None else []
            for t in py:
                versions = [p["version"] for p in pinned if p["name"] == t["name"]]
                ok = bool(versions) and t["version"] not in versions and all(version_key(v) >= version_key(t["to"]) for v in versions)
                (done if ok else failed).append(t | {"now": t["to"] if ok else None})
            if req is not None and new_text != text:
                changed["requirements.txt"] = new_text.encode()
        blocked = dict(base["blocked"])
        for t in failed:
            blocked[target_key(t)] = now
        base = {**base, "blocked": blocked, "failed": failed}
        if not done or not changed:
            rec = self._save(app["id"], {**base, "state": "failed", "error": "the new versions could not be installed "
                                         "within the ranges of package.json", "targets": todo})
            self._tell(app, "Automatic dependency upgrade not possible", ", ".join(t["name"] for t in todo), "alert")
            return rec
        names = ", ".join(f"{t['name']} {t['version']} → {t['now']}" for t in done)
        message = f"Update vulnerable dependencies: {names}"[:300]
        body = "Security fixes found by Mas in the OSV database:\n\n" + "\n".join(
            f"- {t['name']} {t['version']} → {t['now']}" for t in done) + (
            "\n\nNot upgraded:\n" + "\n".join(f"- {t['name']} {t['version']}" for t in failed) if failed else "")
        hashes = {p: hashlib.sha256(c).hexdigest() for p, c in changed.items()}
        rec = {**base, "targets": todo, "done": done, "parent": head, "hashes": hashes, "message": message}
        if mode == "pr":
            name = f"mas/deps-{datetime.fromtimestamp(now, timezone.utc):%Y%m%d-%H%M}"
            gh.create_branch(inst, repo, name, head)
            sha = gh.commit_files(inst, repo, name, changed, message + "\n\n" + body, head)
            pr = gh.open_pull(inst, repo, name, branch, message[:120], body)
            rec = self._save(app["id"], {**rec, "state": "pr", "sha": sha, "branch": name, "pr": pr})
            self._tell(app, "Pull request: vulnerable dependencies upgraded", f"{names}\n{pr.get('url') or ''}")
            return rec
        sha = gh.commit_files(inst, repo, branch, changed, message + "\n\n" + body, head)
        rec = self._save(app["id"], {**rec, "state": "deploying", "sha": sha, "branch": branch, "committed_at": _now_iso(now)})
        if not app.get("auto_deploy"):
            self._deploy(app, sha, rec)
        self._tell(app, "Vulnerable dependencies upgraded", f"{names}. Commit {sha[:8]} on {branch}, deploying now.")
        return rec

    def _deploy(self, app: dict, sha: str, rec: dict) -> None:
        if self.deployer is None:  # the CLI: the server deploys it, or the person does
            return
        try:
            self.deployer.enqueue(app["id"], "deps", "mas", sha)
            rec["enqueued"] = True
        except Exception as exc:  # noqa: BLE001 - e.g. variables waiting: said in the state
            rec["error"] = f"could not deploy: {exc}"[:500]
        self._save(app["id"], rec)

    # -- following the deployment ------------------------------------------------------------------

    def watch(self, now: float | None = None) -> list[str]:
        """The updates being deployed: live → done; failed → the files are put back."""
        now = now or time.time()
        moved = []
        for app in self.db.list_apps():
            rec = self.state(app["id"])
            if rec.get("state") == "pr" and (rec.get("pr") or {}).get("number"):
                self._watch_pull(app, rec, now)
                continue
            if rec.get("state") != "deploying" or not rec.get("sha"):
                continue
            releases = [r for r in self.db.list_releases(app["id"], 30) if r.get("sha") == rec["sha"]]
            final = next((r for r in releases if r["status"] in ("live", "superseded")), None) or next(
                (r for r in releases if r["status"] == "failed"), None)
            age = now - _ts(rec.get("committed_at"))
            if final is None:
                if not releases and age > WEBHOOK_GRACE and not rec.get("enqueued"):
                    self._deploy(app, rec["sha"], rec)
                elif age > GIVE_UP:
                    self._save(app["id"], {**rec, "state": "failed", "error": "the upgrade was never deployed"})
                    moved.append(app["id"])
                continue
            if final["status"] in ("live", "superseded"):
                self._save(app["id"], {**rec, "state": "done", "release_id": final["id"], "finished_at": _now_iso(now)})
                self.db.audit("deps", "deps-update", True, service_id=app["id"], detail=f"release {final['id']} live")
            else:
                self._revert(app, rec, final, now)
            moved.append(app["id"])
        return moved

    def _watch_pull(self, app: dict, rec: dict, now: float) -> None:
        """A pull request merged or closed lets the next audit decide again (merged: its release fixes it)."""
        if now - _ts(rec.get("checked_pr_at")) < 3600:
            return
        gh = self.github()
        if gh is None:
            return
        try:
            state = gh.pull_state(app["installation_id"], app["repo"], rec["pr"]["number"])
        except Exception:  # noqa: BLE001 - GitHub down: next hour
            state = "open"
        if state == "open":
            self._save(app["id"], {**rec, "checked_pr_at": _now_iso(now)})
        else:
            self._save(app["id"], {**rec, "state": "idle", "pr_closed_at": _now_iso(now), "audit_at": None})

    def _revert(self, app: dict, rec: dict, release: dict, now: float) -> None:
        blocked = dict(rec.get("blocked") or {})
        for t in rec.get("done") or []:
            blocked[target_key(t)] = now
        rec = {**rec, "blocked": blocked, "release_id": release["id"], "finished_at": _now_iso(now)}
        why = (release.get("error") or "the deployment failed")[:300]
        gh = self.github()
        try:
            if gh is None:
                raise UpdateError("GitHub is not connected")
            inst, repo, branch = app["installation_id"], app["repo"], rec["branch"]
            head = gh.head(inst, repo, branch)
            restore: dict[str, bytes | None] = {}
            for path, digest in (rec.get("hashes") or {}).items():
                current, _ = gh.read_file(inst, repo, path, head)
                if current is None or hashlib.sha256(current).hexdigest() != digest:
                    raise UpdateError(f"{path} changed since the upgrade: put it back by hand")
                original, _ = gh.read_file(inst, repo, path, rec["parent"])
                restore[path] = original
            sha = gh.commit_files(inst, repo, branch, restore,
                                  f"Revert the dependency upgrade {rec['sha'][:8]}: release {release['id']} failed", head)
            self._save(app["id"], {**rec, "state": "reverted", "revert_sha": sha, "error": why})
            self._tell(app, "Dependency upgrade undone",
                       f"Release {release['id']} failed ({why}); the previous release kept serving and the files are back "
                       f"as they were (commit {sha[:8]}). The upgrade waits for a person.", "alert")
        except Exception as exc:  # noqa: BLE001
            self._save(app["id"], {**rec, "state": "failed", "error": f"{why}; could not undo it: {exc}"[:2000]})
            self._tell(app, "Dependency upgrade failed", f"Release {release['id']} failed ({why}) and Mas could not undo "
                       f"the commit {rec['sha'][:8]}: {exc}", "alert")

    # -- schedule ----------------------------------------------------------------------------------

    def tick(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        try:
            self.watch(now)
        except Exception:  # noqa: BLE001
            log.exception("watching the dependency upgrades failed")
        if self.mode() == "off" or not self.audit.settings()["enabled"] or self.github() is None:
            return []
        for app in self.audit.apps():
            res = self.audit.latest(app["id"])
            rec = self.state(app["id"])
            if not res or not res.get("findings") or not app.get("installation_id") or rec.get("state") in ("deploying", "pr"):
                continue
            if rec.get("audit_at") == res.get("at"):
                continue  # this result was already handled
            todo, _ = plan(res["findings"], {k: float(v) for k, v in (rec.get("blocked") or {}).items()}, now)
            self._save(app["id"], {**rec, "audit_at": res.get("at")})
            if not todo:
                continue
            try:
                self.update(app, now)
            except UpdateError as exc:
                log.warning("dependency upgrade of %s: %s", app["id"], exc)
            rec = self.state(app["id"])
            self._save(app["id"], {**rec, "audit_at": res.get("at")})
            return [app["id"]]  # one app per tick: npm takes a while
        return []
