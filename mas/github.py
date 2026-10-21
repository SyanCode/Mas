"""GitHub App: JWT, installation tokens, repository API, webhook signatures.

Talks to api.github.com only. Tokens never appear in command lines: git gets
them through GIT_CONFIG_* environment variables (see ``git_auth_env``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from mas.detect_type import classify
from mas.models import ServiceType

API = "https://api.github.com"
USER_AGENT = "mas-server-manager"

# (method, url, headers, body) -> (status, parsed JSON or None)
Opener = Callable[[str, str, dict, bytes | None], tuple[int, Any]]


class GitHubError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: int, private_key_pem: bytes, now: float | None = None) -> str:
    """RS256 JWT authenticating as the App (valid 9 minutes)."""
    now = int(now or time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def verify_signature(secret: str | None, body: bytes, header: str | None) -> bool:
    """Check ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body)."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def git_auth_env(token: str) -> dict[str, str]:
    """Environment making git send the installation token to github.com only."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _urllib_opener(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError(f"cannot reach GitHub: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def _request(opener: Opener, method: str, path: str, auth: str | None = None, body: dict | None = None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
               "X-GitHub-Api-Version": "2022-11-28"}
    if auth:
        headers["Authorization"] = auth
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    status, payload = opener(method, path if path.startswith("http") else API + path, headers, data)
    if status >= 400:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise GitHubError(f"GitHub API {method} {path}: HTTP {status} {message or ''}".strip())
    return payload


def convert_manifest(code: str, opener: Opener = _urllib_opener) -> dict:
    """Finish the manifest flow: returns id, slug, pem, webhook_secret…"""
    return _request(opener, "POST", f"/app-manifests/{code}/conversions")


class GitHubApp:
    def __init__(self, app_id: int, private_key_pem: bytes, opener: Opener = _urllib_opener) -> None:
        self.app_id = app_id
        self.private_key_pem = private_key_pem
        self.opener = opener
        self._tokens: dict[int, tuple[str, float]] = {}

    def _as_app(self, method: str, path: str, body: dict | None = None) -> Any:
        return _request(self.opener, method, path, f"Bearer {app_jwt(self.app_id, self.private_key_pem)}", body)

    def token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] > time.time() + 120:
            return cached[0]
        data = self._as_app("POST", f"/app/installations/{installation_id}/access_tokens")
        self._tokens[installation_id] = (data["token"], time.time() + 3000)
        return data["token"]

    def _as_installation(self, installation_id: int, path: str, method: str = "GET", body: dict | None = None) -> Any:
        return _request(self.opener, method, path, f"token {self.token(installation_id)}", body)

    def permissions(self, installation_id: int) -> dict[str, str]:
        return self._as_app("GET", f"/app/installations/{installation_id}").get("permissions") or {}

    def head(self, installation_id: int, repo: str, branch: str) -> str:
        ref = self._as_installation(installation_id, f"/repos/{repo}/git/ref/heads/{quote(branch, safe='')}")
        return ref["object"]["sha"]

    def tree(self, installation_id: int, repo: str, ref: str) -> list[dict]:
        """Every file of the repository at ``ref``: [{path, size}]."""
        data = self._as_installation(installation_id, f"/repos/{repo}/git/trees/{quote(ref, safe='')}?recursive=1")
        return [{"path": t["path"], "size": t.get("size", 0)} for t in data.get("tree", []) if t.get("type") == "blob"]

    def list_dir(self, installation_id: int, repo: str, path: str, ref: str) -> list[dict]:
        """Files directly in ``path`` at ``ref``: [{name, path, sha}], [] when the folder does not exist."""
        try:
            items = self._as_installation(installation_id,
                                          f"/repos/{repo}/contents/{quote(path)}?ref={quote(ref, safe='')}")
        except GitHubError as exc:
            if "HTTP 404" in str(exc):
                return []
            raise
        if not isinstance(items, list):
            return []
        return [{"name": i["name"], "path": i["path"], "sha": i["sha"]} for i in items if i.get("type") == "file"]

    def read_file(self, installation_id: int, repo: str, path: str, ref: str) -> tuple[bytes | None, str | None]:
        """(content, blob sha) of a file, (None, None) when it does not exist."""
        try:
            item = self._as_installation(installation_id, f"/repos/{repo}/contents/{quote(path)}?ref={quote(ref, safe='')}")
        except GitHubError as exc:
            if "HTTP 404" in str(exc):
                return None, None
            raise
        if not isinstance(item, dict) or item.get("type") != "file":
            return None, None
        if item.get("content") or not item.get("size"):
            return base64.b64decode(item.get("content") or ""), item["sha"]
        blob = self._as_installation(installation_id, f"/repos/{repo}/git/blobs/{item['sha']}")  # > 1 MB
        return base64.b64decode(blob.get("content") or ""), item["sha"]

    def commit_files(self, installation_id: int, repo: str, branch: str, files: dict[str, bytes | None],
                     message: str, parent: str) -> str:
        """One commit with every file on top of ``parent`` (None deletes it); refused if the branch moved meanwhile."""
        base_tree = self._as_installation(installation_id, f"/repos/{repo}/git/commits/{parent}")["tree"]["sha"]
        tree = []
        for path, content in files.items():
            if content is None:
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            blob = self._as_installation(installation_id, f"/repos/{repo}/git/blobs", "POST",
                                         {"content": base64.b64encode(content).decode(), "encoding": "base64"})
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        new_tree = self._as_installation(installation_id, f"/repos/{repo}/git/trees", "POST",
                                         {"base_tree": base_tree, "tree": tree})
        commit = self._as_installation(installation_id, f"/repos/{repo}/git/commits", "POST",
                                       {"message": message, "tree": new_tree["sha"], "parents": [parent]})
        self._as_installation(installation_id, f"/repos/{repo}/git/refs/heads/{quote(branch, safe='')}", "PATCH",
                              {"sha": commit["sha"], "force": False})
        return commit["sha"]

    def app_info(self) -> dict:
        return self._as_app("GET", "/app")

    def installations(self) -> list[dict]:
        return self._as_app("GET", "/app/installations?per_page=100")

    def installation_for(self, repo: str) -> int:
        return int(self._as_app("GET", f"/repos/{repo}/installation")["id"])

    def repos(self) -> list[dict]:
        out = []
        for inst in self.installations():
            page = 1
            while True:
                data = self._as_installation(inst["id"], f"/installation/repositories?per_page=100&page={page}")
                repos = data.get("repositories", [])
                for r in repos:
                    out.append({"full_name": r["full_name"], "default_branch": r.get("default_branch", "main"),
                                "private": r.get("private", False), "installation_id": inst["id"]})
                if len(repos) < 100:
                    break
                page += 1
        return sorted(out, key=lambda r: r["full_name"].lower())

    def branches(self, installation_id: int, repo: str) -> list[str]:
        return [b["name"] for b in self._as_installation(installation_id, f"/repos/{repo}/branches?per_page=100")]

    def commit(self, installation_id: int, repo: str, ref: str) -> dict:
        data = self._as_installation(installation_id, f"/repos/{repo}/commits/{quote(ref, safe='')}")
        msg = (data.get("commit", {}).get("message") or "").splitlines()
        return {"sha": data["sha"], "message": msg[0] if msg else "",
                "author": data.get("commit", {}).get("author", {}).get("name")}

    def detect_type(self, installation_id: int, repo: str, ref: str) -> ServiceType:
        ref_q = quote(ref, safe="")
        listing = self._as_installation(installation_id, f"/repos/{repo}/contents?ref={ref_q}")
        names = {item["name"] for item in listing if isinstance(item, dict)}
        deps: dict = {}
        if "package.json" in names:
            item = self._as_installation(installation_id, f"/repos/{repo}/contents/package.json?ref={ref_q}")
            try:
                pkg = json.loads(base64.b64decode(item.get("content", "")))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            except (ValueError, TypeError, AttributeError):
                deps = {}
        found = classify(names, deps)
        if found:
            return found
        return ServiceType.STATIC if "index.html" in names or "public" in names else ServiceType.UNKNOWN


def repo_from_remote(remote: str | None) -> str | None:
    """'git@github.com:acme/web.git' / 'https://github.com/acme/web' -> 'acme/web'."""
    if not remote:
        return None
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/", "http://github.com/"):
        if remote.startswith(prefix):
            name = remote[len(prefix):].removesuffix(".git").strip("/")
            return name if name.count("/") == 1 else None
    return None
