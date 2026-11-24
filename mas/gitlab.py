"""GitLab: a second source of code next to GitHub, a GitLab of this server found by itself, and a mirror.

- ``GitLab`` speaks the API v4 of a GitLab (gitlab.com or a self-hosted one) with an access token, and answers the same
  calls as ``mas.github.GitHubApp`` (commit, tree, read_file, commit_files, comment, pull_state…). The deployer, the
  previews and the robot use it unchanged for the apps whose ``source`` is ``gitlab``. The ``installation_id``
  arguments are kept for that and ignored: one token reaches every project it is a member of.
- ``detect`` finds a GitLab running on this machine (the Omnibus package or a ``gitlab/gitlab-ce|ee`` container) and
  its address; the cluster asks its other servers the same.
- Webhooks: ``push_event`` and ``merge_request_event`` turn GitLab's hooks into what the GitHub code already handles
  (push → deployment, merge request → a preview of the branch, removed when the merge request closes).
- ``Mirror`` keeps a GitLab project in sync with the GitHub repository of an app (every branch and tag), after each
  push and every half hour: the code is also on your own GitLab, for its CI, its reviews, or as a backup of GitHub.

Tokens never appear in a command line: git gets them through GIT_CONFIG_* environment variables, for the GitLab's
address only. The token is kept encrypted in the database (the same key as the app variables).
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from mas.detect_type import classify
from mas.host import DOCKER_SOCKET, Host
from mas.models import ServiceType

S_URL, S_TOKEN, S_SECRET = "gitlab:url", "gitlab:token", "gitlab:webhook_secret"
USER_AGENT = "mas-server-manager"
IMAGES = ("gitlab/gitlab-ce", "gitlab/gitlab-ee", "gitlab-ce", "gitlab-ee")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+){1,10}$")  # group/subgroup/project

Opener = Callable[[str, str, dict, bytes | None], tuple[int, Any]]


from mas.github import GitHubError  # noqa: E402


class GitLabError(GitHubError):  # caught everywhere the code-host errors are (deployer, previews, robot)
    pass


def _urllib_opener(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitLabError(f"cannot reach GitLab: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def clean_url(url: str) -> str:
    """The address of a GitLab: https, or http for this machine or a private network (a GitLab next to Mas)."""
    url = (url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.query or parts.fragment or parts.username:
        raise GitLabError("give the address of the GitLab, e.g. https://gitlab.example.com")
    if parts.scheme == "http":
        try:
            local = ipaddress.ip_address(parts.hostname)
            ok = local.is_loopback or local.is_private
        except ValueError:
            ok = parts.hostname in ("localhost",) or parts.hostname.endswith((".local", ".internal", ".lan"))
        if not ok:
            raise GitLabError("a GitLab on the internet must be reached over https")
    return url


def check_repo(repo: str) -> str:
    repo = (repo or "").strip().strip("/").removesuffix(".git")
    if not REPO_RE.match(repo) or ".." in repo:
        raise GitLabError(f"invalid GitLab project path: {repo!r} (group/project)")
    return repo


def auth_env(base: str, token: str) -> dict[str, str]:
    """Environment making git send the token to this GitLab only."""
    basic = base64.b64encode(f"oauth2:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{base}/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}", "GIT_TERMINAL_PROMPT": "0"}


class GitLab:
    source = "gitlab"

    def __init__(self, url: str, token: str, opener: Opener = _urllib_opener) -> None:
        self.url = clean_url(url)
        self.api = f"{self.url}/api/v4"
        self._token = token
        self.opener = opener

    # -- plumbing ------------------------------------------------------------------------------------

    def _req(self, method: str, path: str, body: dict | None = None, ok404: bool = False) -> Any:
        headers = {"PRIVATE-TOKEN": self._token, "Accept": "application/json", "User-Agent": USER_AGENT}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        status, payload = self.opener(method, self.api + path, headers, data)
        if status == 404 and ok404:
            return None
        if status >= 400:
            message = payload.get("message") or payload.get("error") if isinstance(payload, dict) else None
            raise GitLabError(f"GitLab API {method} {path.split('?')[0]}: HTTP {status} {message or ''}".strip())
        return payload

    @staticmethod
    def _p(repo: str) -> str:
        return "/projects/" + quote(check_repo(repo), safe="")

    def _pages(self, path: str, limit: int = 20) -> list:
        out: list = []
        sep = "&" if "?" in path else "?"
        for page in range(1, limit + 1):
            items = self._req("GET", f"{path}{sep}per_page=100&page={page}") or []
            out.extend(items)
            if len(items) < 100:
                break
        return out

    # -- what the GitHub client offers too -----------------------------------------------------------------

    def token(self, installation_id: int | None = None) -> str:
        return self._token

    def installation_for(self, repo: str) -> int:
        return 0

    def clone_url(self, repo: str) -> str:
        return f"{self.url}/{check_repo(repo)}.git"

    def auth_env(self, token: str) -> dict[str, str]:
        return auth_env(self.url, token)

    def head(self, installation_id: int | None, repo: str, branch: str) -> str:
        return self._req("GET", f"{self._p(repo)}/repository/branches/{quote(branch, safe='')}")["commit"]["id"]

    def tree(self, installation_id: int | None, repo: str, ref: str) -> list[dict]:
        items = self._pages(f"{self._p(repo)}/repository/tree?recursive=true&ref={quote(ref, safe='')}", limit=50)
        return [{"path": i["path"], "size": 0} for i in items if i.get("type") == "blob"]

    def list_dir(self, installation_id: int | None, repo: str, path: str, ref: str) -> list[dict]:
        items = self._req("GET", f"{self._p(repo)}/repository/tree?path={quote(path, safe='')}&ref={quote(ref, safe='')}&per_page=100",
                          ok404=True) or []
        return [{"name": i["name"], "path": i["path"], "sha": i["id"], "size": None} for i in items if i.get("type") == "blob"]

    def read_file(self, installation_id: int | None, repo: str, path: str, ref: str) -> tuple[bytes | None, str | None]:
        item = self._req("GET", f"{self._p(repo)}/repository/files/{quote(path, safe='')}?ref={quote(ref, safe='')}", ok404=True)
        if not isinstance(item, dict):
            return None, None
        return base64.b64decode(item.get("content") or ""), item.get("blob_id")

    def _exists(self, repo: str, path: str, ref: str) -> bool:
        return self._req("GET", f"{self._p(repo)}/repository/files/{quote(path, safe='')}?ref={quote(ref, safe='')}",
                         ok404=True) is not None

    def commit_files(self, installation_id: int | None, repo: str, branch: str, files: dict[str, bytes | None],
                     message: str, parent: str) -> str:
        """One commit on ``branch``; refused when the branch moved since ``parent`` (someone pushed meanwhile)."""
        if self.head(None, repo, branch) != parent:
            raise GitLabError(f"{branch} moved since it was read: read it again")
        actions = []
        for path, content in files.items():
            if content is None:
                actions.append({"action": "delete", "file_path": path})
            else:
                actions.append({"action": "update" if self._exists(repo, path, parent) else "create", "file_path": path,
                                "content": base64.b64encode(content).decode(), "encoding": "base64"})
        commit = self._req("POST", f"{self._p(repo)}/repository/commits",
                           {"branch": branch, "commit_message": message, "actions": actions})
        return commit["id"]

    def create_branch(self, installation_id: int | None, repo: str, branch: str, sha: str) -> None:
        self._req("POST", f"{self._p(repo)}/repository/branches?branch={quote(branch, safe='')}&ref={quote(sha, safe='')}")

    def open_pull(self, installation_id: int | None, repo: str, head: str, base: str, title: str, body: str) -> dict:
        mr = self._req("POST", f"{self._p(repo)}/merge_requests",
                       {"source_branch": head, "target_branch": base, "title": title, "description": body})
        return {"number": mr.get("iid"), "url": mr.get("web_url")}

    def get_commit(self, installation_id: int | None, repo: str, sha: str) -> dict:
        c = self._req("GET", f"{self._p(repo)}/repository/commits/{quote(sha, safe='')}")
        diff = self._req("GET", f"{self._p(repo)}/repository/commits/{quote(sha, safe='')}/diff?per_page=100") or []
        files = []
        for d in diff:
            status = "added" if d.get("new_file") else "removed" if d.get("deleted_file") else "renamed" if d.get("renamed_file") else "modified"
            files.append({"filename": d.get("new_path"), "status": status, "sha": None,
                          "previous_filename": d.get("old_path") if d.get("renamed_file") else None})
        return {"parents": c.get("parent_ids") or [], "files": files, "truncated": len(diff) >= 100}

    def pull_state(self, installation_id: int | None, repo: str, number: int) -> str:
        mr = self._req("GET", f"{self._p(repo)}/merge_requests/{int(number)}", ok404=True)
        if mr is None:
            return "gone"
        return "open" if mr.get("state") == "opened" else "closed"

    def comment(self, installation_id: int | None, repo: str, number: int, text: str) -> None:
        self._req("POST", f"{self._p(repo)}/merge_requests/{int(number)}/notes", {"body": text[:60000]})

    def create_check_run(self, installation_id: int | None, repo: str, head_sha: str, name: str, conclusion: str,
                         title: str, summary: str, text: str = "") -> None:
        """A commit status (GitLab's equivalent of a check)."""
        state = {"success": "success", "neutral": "success", "skipped": "skipped"}.get(conclusion, "failed")
        self._req("POST", f"{self._p(repo)}/statuses/{quote(head_sha, safe='')}",
                  {"state": state, "name": name[:100], "description": title[:250]})

    def repos(self) -> list[dict]:
        items = self._pages("/projects?membership=true&archived=false&order_by=last_activity_at&simple=true", limit=10)
        out = []
        for p in items:
            ns = p.get("namespace") or {}
            out.append({"full_name": p["path_with_namespace"], "default_branch": p.get("default_branch") or "main",
                        "private": p.get("visibility") != "public", "installation_id": 0, "source": "gitlab",
                        "owner": ns.get("full_path") or p["path_with_namespace"].rsplit("/", 1)[0],
                        "owner_type": "Organization" if ns.get("kind") == "group" else "User",
                        "description": (p.get("description") or "")[:300], "language": None,
                        "pushed_at": p.get("last_activity_at"), "archived": False})
        return sorted(out, key=lambda r: r["full_name"].lower())

    def default_branch(self, installation_id: int | None, repo: str) -> str:
        return self._req("GET", self._p(repo)).get("default_branch") or "main"

    def branches(self, installation_id: int | None, repo: str) -> list[str]:
        return [b["name"] for b in self._pages(f"{self._p(repo)}/repository/branches", limit=3)]

    def commit(self, installation_id: int | None, repo: str, ref: str) -> dict:
        c = self._req("GET", f"{self._p(repo)}/repository/commits/{quote(ref, safe='')}")
        return {"sha": c["id"], "message": (c.get("title") or "").strip(), "author": c.get("author_name")}

    def detect_type(self, installation_id: int | None, repo: str, ref: str) -> ServiceType:
        listing = self._req("GET", f"{self._p(repo)}/repository/tree?ref={quote(ref, safe='')}&per_page=100") or []
        names = {i["name"] for i in listing if isinstance(i, dict)}
        deps: dict = {}
        scripts: dict | None = None
        if "package.json" in names:
            raw, _ = self.read_file(None, repo, "package.json", ref)
            try:
                pkg = json.loads(raw or b"{}")
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
            except (ValueError, TypeError, AttributeError):
                deps = {}
        found = classify(names, deps, scripts=scripts)
        if found:
            return found
        return ServiceType.STATIC if "index.html" in names or "public" in names else ServiceType.UNKNOWN

    # -- GitLab only ---------------------------------------------------------------------------------------

    def whoami(self) -> dict:
        user = self._req("GET", "/user")
        version = self._req("GET", "/version", ok404=True) or {}
        return {"username": user.get("username"), "name": user.get("name"), "version": version.get("version")}

    def ensure_hook(self, repo: str, url: str, secret: str) -> bool:
        """The webhook of a project pointing to Mas (push and merge request events); True when it was added."""
        hooks = self._req("GET", f"{self._p(repo)}/hooks") or []
        if any(h.get("url") == url for h in hooks):
            return False
        self._req("POST", f"{self._p(repo)}/hooks", {"url": url, "token": secret, "push_events": True,
                                                       "merge_requests_events": True, "enable_ssl_verification": True})
        return True

    def ensure_project(self, path: str, description: str = "") -> dict:
        """The project at ``path`` (group/name), created private when it does not exist yet."""
        path = check_repo(path)
        found = self._req("GET", self._p(path), ok404=True)
        if found:
            return {"full_name": found["path_with_namespace"], "created": False}
        group, name = path.rsplit("/", 1)
        ns = self._req("GET", f"/namespaces/{quote(group, safe='')}", ok404=True)
        if not ns:
            raise GitLabError(f"no group or user {group!r} on this GitLab (create it, or pick another path)")
        created = self._req("POST", "/projects", {"name": name, "path": name, "namespace_id": ns["id"],
                                                    "visibility": "private", "description": description[:200]})
        return {"full_name": created["path_with_namespace"], "created": True}


# -- settings -------------------------------------------------------------------------------------------


def load(db, fernet) -> dict | None:
    """{url, token, secret} of the connected GitLab, or None."""
    url, token = db.get_setting(S_URL), db.get_setting(S_TOKEN)
    if not url or not token:
        return None
    try:
        return {"url": url, "token": fernet.decrypt(token.encode()).decode(),
                "secret": fernet.decrypt((db.get_setting(S_SECRET) or "").encode()).decode()}
    except Exception:  # noqa: BLE001 - the key changed: connect it again
        return None


def save(db, fernet, url: str, token: str) -> dict:
    url = clean_url(url)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{16,200}", token or ""):
        raise GitLabError("the access token looks wrong (glpat-…)")
    db.set_setting(S_URL, url)
    db.set_setting(S_TOKEN, fernet.encrypt(token.encode()).decode())
    if not db.get_setting(S_SECRET):
        db.set_setting(S_SECRET, fernet.encrypt(secrets.token_urlsafe(32).encode()).decode())
    return {"url": url}


def forget(db) -> None:
    for key in (S_URL, S_TOKEN, S_SECRET):
        db.set_setting(key, None)


def verify_token(secret: str | None, header: str | None) -> bool:
    return bool(secret and header and hmac.compare_digest(secret, header))


# -- webhooks → the shapes the GitHub code handles -------------------------------------------------------


def push_event(payload: dict) -> dict | None:
    """{repo, branch, sha, actor, head_commit, commits} of a Push Hook, or None (tag, deleted branch)."""
    ref, sha = payload.get("ref", ""), payload.get("after", "")
    repo = (payload.get("project") or {}).get("path_with_namespace", "")
    if not ref.startswith("refs/heads/") or not sha or set(sha) == {"0"} or not repo:
        return None
    commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
    last = next((c for c in commits if c.get("id") == sha), commits[-1] if commits else {})
    return {"repo": repo, "branch": ref[len("refs/heads/"):], "sha": sha,
            "actor": payload.get("user_username") or payload.get("user_name") or "gitlab",
            "head_commit": {"message": last.get("message") or "", "author": {"name": (last.get("author") or {}).get("name")}},
            "commits": commits}


def merge_request_event(payload: dict) -> dict | None:
    """A Merge Request Hook as a GitHub ``pull_request`` payload (see mas.previews), or None to ignore it."""
    attrs = payload.get("object_attributes") or {}
    project = payload.get("project") or {}
    repo = project.get("path_with_namespace", "")
    action = {"open": "opened", "reopen": "reopened", "update": "synchronize", "close": "closed",
              "merge": "closed"}.get(attrs.get("action", ""))
    if not repo or not action or not isinstance(attrs.get("iid"), int):
        return None
    same = attrs.get("source_project_id") == attrs.get("target_project_id")  # a fork's code never runs here
    source_repo = repo if same else f"fork-{attrs.get('source_project_id')}"
    return {"action": action, "repository": {"full_name": repo},
            "pull_request": {"number": attrs["iid"], "draft": bool(attrs.get("draft") or attrs.get("work_in_progress")),
                             "head": {"ref": attrs.get("source_branch", ""), "sha": (attrs.get("last_commit") or {}).get("id", ""),
                                      "repo": {"full_name": source_repo}},
                             "base": {"ref": attrs.get("target_branch", "")},
                             "user": {"login": (payload.get("user") or {}).get("username") or "gitlab"}}}


# -- a GitLab of this machine ------------------------------------------------------------------------------


def _external_url(text: str) -> str | None:
    m = re.search(r"""^\s*external_url\s+['"]([^'"]+)['"]""", text or "", re.M)
    return m.group(1).rstrip("/") if m else None


def detect(host: Host, services: list | None = None) -> list[dict]:
    """GitLab instances running here: [{kind, name, url, image?, version?, service_id?}]."""
    found: list[dict] = []
    if host.exists("/opt/gitlab/embedded") or host.exists("/etc/gitlab/gitlab.rb"):
        version = (host.read_text("/opt/gitlab/version-manifest.txt") or "").splitlines()[:1]
        found.append({"kind": "omnibus", "name": "gitlab", "url": _external_url(host.read_text("/etc/gitlab/gitlab.rb") or ""),
                      "version": version[0].split()[-1] if version and version[0].split() else None})
    if host.exists(DOCKER_SOCKET):
        try:
            containers = host.docker_get("/containers/json")
        except (OSError, ValueError):
            containers = []
        for c in containers:
            image = (c.get("Image") or "").split("@")[0]
            if not image.split(":")[0].endswith(IMAGES):
                continue
            name = ((c.get("Names") or ["/gitlab"])[0]).lstrip("/")
            url = None
            try:
                env = (host.docker_get(f"/containers/{c['Id']}/json").get("Config") or {}).get("Env") or []
                omnibus = next((e.partition("=")[2] for e in env if e.startswith("GITLAB_OMNIBUS_CONFIG=")), "")
                url = _external_url(omnibus.replace(";", "\n"))
            except (OSError, ValueError, KeyError):
                pass
            port = next((p.get("PublicPort") for p in c.get("Ports") or [] if p.get("PrivatePort") == 80 and p.get("PublicPort")), None)
            found.append({"kind": "docker", "name": name, "image": image, "url": url or (f"http://127.0.0.1:{port}" if port else None)})
    for svc in services or []:  # a reverse proxy in front of it gives the public address
        if any(d.get("name") == svc.unit or d.get("name") == svc.name for d in found) and svc.domains:
            for d in found:
                if d.get("name") in (svc.unit, svc.name) and (not d.get("url") or "127.0.0.1" in d["url"]):
                    d["url"] = f"https://{svc.domains[0]}"
                    d["service_id"] = svc.id
    return found


# -- mirror: the GitHub repository of an app, kept on the GitLab ---------------------------------------------


MIRROR = "mirror:"  # setting mirror:<app id> = {"project", "at", "error"}
MIRROR_EVERY = 1800


class Mirror:
    def __init__(self, host: Host, db, data_dir: str | Path, github: Callable, gitlab: Callable[[], GitLab | None]) -> None:
        self.host, self.db, self.dir, self.github, self.gitlab = host, db, Path(data_dir) / "mirrors", github, gitlab

    def get(self, app_id: str) -> dict | None:
        raw = self.db.get_setting(MIRROR + app_id)
        return json.loads(raw) if raw else None

    def configure(self, app: dict, project: str | None) -> dict | None:
        if not project:
            self.db.set_setting(MIRROR + app["id"], None)
            return None
        if (app.get("source") or "github") != "github" or not app.get("repo"):
            raise GitLabError("only an app deployed from GitHub can be mirrored to GitLab")
        gl = self.gitlab()
        if gl is None:
            raise GitLabError("connect a GitLab first (Settings → GitLab)")
        made = gl.ensure_project(project, f"Mirror of github.com/{app['repo']} (kept in sync by Mas)")
        state = {"project": made["full_name"], "at": None, "error": None}
        self.db.set_setting(MIRROR + app["id"], json.dumps(state))
        return state | {"created": made["created"]}

    def sync(self, app: dict, log: Callable[[str], None] = lambda line: None) -> bool:
        """Fetch every branch and tag from GitHub into a bare copy, then push them to the GitLab project."""
        state = self.get(app["id"])
        gl, gh = self.gitlab(), self.github()
        if not state or gl is None or gh is None:
            return False
        bare = self.dir / f"{app['id']}.git"
        lines: list[str] = []
        say = lambda line: (lines.append(line), log(line))  # noqa: E731
        try:
            if not self.host.exists(str(bare)):
                self.host.makedirs(str(self.dir))
                self._git(["git", "init", "--bare", "--quiet", str(bare)], {}, say)
            installation = app.get("installation_id") or gh.installation_for(app["repo"])
            source = f"https://github.com/{app['repo']}.git"
            from mas.github import git_auth_env
            self._git(["git", "-C", str(bare), "fetch", "--prune", "--quiet", source,
                       "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"], git_auth_env(gh.token(installation)), say)
            self._git(["git", "-C", str(bare), "push", "--prune", "--quiet", gl.clone_url(state["project"]),
                       "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"], gl.auth_env(gl.token()), say)
            state.update(at=time.time(), tried=time.time(), error=None)
            ok = True
        except (GitLabError, OSError, RuntimeError) as exc:
            state.update(tried=time.time(), error=str(exc)[:300] or "\n".join(lines[-3:])[:300])
            ok = False
        except Exception as exc:  # noqa: BLE001 - GitHub errors and the like: kept for the dashboard
            state.update(tried=time.time(), error=str(exc)[:300])
            ok = False
        self.db.set_setting(MIRROR + app["id"], json.dumps(state))
        self.db.audit("mirror", "gitlab-mirror", ok, service_id=app["id"], detail=f"→ {state['project']}" + ("" if ok else f": {state['error']}"))
        return ok

    def _git(self, argv: list[str], env: dict, say) -> None:
        rc = self.host.stream(argv, say, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root", "LANG": "C.UTF-8", **env}, timeout=900)
        if rc != 0:
            raise RuntimeError(f"`{' '.join(argv[:4])}` exited with {rc}")

    def on_push(self, repo: str) -> list[str]:
        done = []
        for app in self.db.list_apps():
            if (app.get("repo") or "").lower() == repo.lower() and (app.get("source") or "github") == "github" and self.get(app["id"]):
                if self.sync(app):
                    done.append(app["id"])
        return done

    def tick(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        done = []
        for app in self.db.list_apps():
            state = self.get(app["id"])
            if state and now - float(state.get("tried") or state.get("at") or 0) >= MIRROR_EVERY and self.sync(app):
                done.append(app["id"])
        return done
