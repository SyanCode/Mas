"""GitLab: the API client (same calls as the GitHub one), webhooks, a GitLab of this server, deploys from it,
merge request previews and the mirror of GitHub repositories into it."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
import server
from fastapi.testclient import TestClient
from test_deploy import SHA1, deploy_once, env  # noqa: F401 - the deploy fixture

from mas import auth, gitlab
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

PASSWORD = "correct horse battery"
TOKEN = "glpat-" + "x" * 20


class Api:
    """A GitLab answering from a dict of (method, path) → payload, keeping every request."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, method, url, headers, body):
        path = url.split("/api/v4", 1)[1]
        self.seen.append((method, path, headers, json.loads(body) if body else None))
        for (m, p), answer in self.routes.items():
            if m == method and (path == p or path.split("?")[0] == p):
                return answer if isinstance(answer, tuple) else (200, answer)
        return 404, {"message": "404 Not found"}


def test_addresses_paths_and_git_credentials():
    assert gitlab.clean_url("https://gitlab.example.com/") == "https://gitlab.example.com"
    assert gitlab.clean_url("http://127.0.0.1:8929") == "http://127.0.0.1:8929"
    assert gitlab.clean_url("http://10.0.0.5") == "http://10.0.0.5"
    for bad in ("http://gitlab.example.com", "ftp://x", "https://u:p@gitlab.example.com", "gitlab.example.com", ""):
        with pytest.raises(gitlab.GitLabError):
            gitlab.clean_url(bad)
    assert gitlab.check_repo("group/sub/app.git") == "group/sub/app"
    for bad in ("app", "a/../b", "a b/c", "/"):
        with pytest.raises(gitlab.GitLabError):
            gitlab.check_repo(bad)
    gl = gitlab.GitLab("https://gitlab.example.com", TOKEN, Api({}))
    assert gl.clone_url("acme/web") == "https://gitlab.example.com/acme/web.git"  # no token in the command line
    env = gl.auth_env(TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://gitlab.example.com/.extraheader"
    assert base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]).decode() == f"oauth2:{TOKEN}"


def test_the_client_answers_like_the_github_one():
    content = base64.b64encode(b'{"dependencies": {"next": "15"}, "scripts": {"build": "next build"}}').decode()
    api = Api({
        ("GET", "/projects/acme%2Fweb/repository/branches/main"): {"commit": {"id": "h" * 40}},
        ("GET", "/projects/acme%2Fweb/repository/commits/main"): {"id": "c" * 40, "title": "Fix menu", "author_name": "Ann"},
        ("GET", "/projects/acme%2Fweb/repository/files/package.json"): {"content": content, "blob_id": "b1"},
        ("GET", "/projects/acme%2Fweb/repository/tree"): [{"name": "package.json", "path": "package.json", "type": "blob", "id": "1"}],
        ("POST", "/projects/acme%2Fweb/repository/commits"): {"id": "n" * 40},
        ("GET", "/projects/acme%2Fweb/merge_requests/4"): {"state": "merged"},
        ("GET", "/projects/acme%2Fweb/merge_requests/5"): {"state": "opened"},
        ("POST", "/projects/acme%2Fweb/merge_requests/5/notes"): {},
        ("GET", "/projects/acme%2Fweb/hooks"): [],
        ("POST", "/projects/acme%2Fweb/hooks"): {},
        ("GET", "/projects"): [{"path_with_namespace": "acme/web", "default_branch": "main", "visibility": "private",
                                 "namespace": {"full_path": "acme", "kind": "group"}}],
    })
    gl = gitlab.GitLab("https://gitlab.example.com", TOKEN, api)
    assert gl.head(None, "acme/web", "main") == "h" * 40
    assert gl.commit(7, "acme/web", "main") == {"sha": "c" * 40, "message": "Fix menu", "author": "Ann"}
    assert gl.read_file(None, "acme/web", "package.json", "main")[1] == "b1"
    assert gl.read_file(None, "acme/web", "missing.txt", "main") == (None, None)
    assert gl.detect_type(None, "acme/web", "main").value == "nextjs"
    assert gl.pull_state(None, "acme/web", 4) == "closed" and gl.pull_state(None, "acme/web", 5) == "open"
    assert gl.pull_state(None, "acme/web", 6) == "gone"
    # one commit, update or create per file, refused when the branch moved
    assert gl.commit_files(None, "acme/web", "main", {"package.json": b"{}", "new.txt": b"hi", "old.txt": None},
                           "robot: x", "h" * 40) == "n" * 40
    actions = {a["file_path"]: a["action"] for a in api.seen[-1][3]["actions"]}
    assert actions == {"package.json": "update", "new.txt": "create", "old.txt": "delete"}
    with pytest.raises(gitlab.GitLabError, match="moved"):
        gl.commit_files(None, "acme/web", "main", {"a": b"1"}, "x", "o" * 40)
    gl.comment(None, "acme/web", 5, "Preview: https://mr5.web.example.com")
    assert gl.ensure_hook("acme/web", "https://mas.example.com/api/gitlab/webhook", "s3cret") is True
    hook = api.seen[-1][3]
    assert hook["token"] == "s3cret" and hook["merge_requests_events"] and hook["enable_ssl_verification"]
    api.routes[("POST", "/projects/acme%2Fweb/statuses/" + "d" * 40)] = {}
    gl.create_check_run(None, "acme/web", "d" * 40, "Mas deploy", "failure", "Deployment failed", "x")
    assert api.seen[-1][3] == {"state": "failed", "name": "Mas deploy", "description": "Deployment failed"}
    repos = gl.repos()
    assert repos[0]["full_name"] == "acme/web" and repos[0]["source"] == "gitlab" and repos[0]["owner_type"] == "Organization"
    assert all(h["PRIVATE-TOKEN"] == TOKEN for _, _, h, _ in api.seen)


def test_webhooks_become_what_the_github_code_handles():
    push = {"ref": "refs/heads/main", "after": "a" * 40, "user_username": "ann",
            "project": {"path_with_namespace": "acme/web"}, "commits": [{"id": "a" * 40, "message": "Fix", "author": {"name": "Ann"}}]}
    assert gitlab.push_event(push)["branch"] == "main" and gitlab.push_event(push)["actor"] == "ann"
    assert gitlab.push_event({**push, "ref": "refs/tags/v1"}) is None
    assert gitlab.push_event({**push, "after": "0" * 40}) is None
    mr = {"project": {"path_with_namespace": "acme/web"}, "user": {"username": "bob"},
          "object_attributes": {"iid": 5, "action": "open", "source_branch": "menu", "target_branch": "main",
                                "source_project_id": 1, "target_project_id": 1, "last_commit": {"id": "b" * 40}}}
    pr = gitlab.merge_request_event(mr)
    assert pr["action"] == "opened" and pr["pull_request"]["number"] == 5
    assert pr["pull_request"]["head"] == {"ref": "menu", "sha": "b" * 40, "repo": {"full_name": "acme/web"}}
    assert gitlab.merge_request_event({**mr, "object_attributes": {**mr["object_attributes"], "action": "merge"}})["action"] == "closed"
    fork = gitlab.merge_request_event({**mr, "object_attributes": {**mr["object_attributes"], "source_project_id": 9}})
    assert fork["pull_request"]["head"]["repo"]["full_name"] != "acme/web"  # a fork's code never runs here
    assert gitlab.merge_request_event({**mr, "object_attributes": {**mr["object_attributes"], "action": "approved"}}) is None


def test_a_gitlab_of_this_server_is_found(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    assert gitlab.detect(host) == [] or all(g["kind"] == "docker" for g in gitlab.detect(host))
    host.write_text("/etc/gitlab/gitlab.rb", "# config\nexternal_url 'https://git.example.com'\n")
    host.write_text("/opt/gitlab/version-manifest.txt", "gitlab-ce 17.5.1\n")
    host.makedirs("/opt/gitlab/embedded")
    found = gitlab.detect(host)
    assert found[0] == {"kind": "omnibus", "name": "gitlab", "url": "https://git.example.com", "version": "17.5.1"}
    host.write_text("/var/run/docker.sock", "")
    containers = [{"Id": "g" * 64, "Names": ["/gitlab"], "Image": "gitlab/gitlab-ce:17.5.1-ce.0",
                   "Ports": [{"PrivatePort": 80, "PublicPort": 8929}]},
                  {"Id": "r" * 64, "Names": ["/redis"], "Image": "redis:7"}]
    inspect = {"Config": {"Env": ["GITLAB_OMNIBUS_CONFIG=external_url 'https://code.example.com'; nginx['listen_port'] = 80"]}}
    host.docker_get = lambda path: containers if path == "/containers/json" else inspect
    docker = [g for g in gitlab.detect(host) if g["kind"] == "docker"]
    assert docker == [{"kind": "docker", "name": "gitlab", "image": "gitlab/gitlab-ce:17.5.1-ce.0", "url": "https://code.example.com"}]


class FakeGitLab:
    source = "gitlab"
    url = "https://gitlab.example.com"

    def token(self, installation_id=None):
        return TOKEN

    def installation_for(self, repo):
        return 0

    def commit(self, installation_id, repo, ref):
        return {"sha": SHA1, "message": "from gitlab", "author": "Ann"}

    def clone_url(self, repo):
        return f"{self.url}/{repo}.git"

    def auth_env(self, token):
        return gitlab.auth_env(self.url, token)


def test_an_app_deploys_from_gitlab(env):  # noqa: F811
    db, deployer, calls = env["db"], env["deployer"], env["calls"]
    db.update_app("web", source="gitlab", repo="acme/front/web")
    deployer.gitlab = lambda: FakeGitLab()
    rid, ok = deploy_once(env, SHA1)
    assert ok, deployer.log_path("web", rid).read_text()
    clone = next((argv, e) for argv, e in calls if argv[:2] == ["git", "clone"])
    assert "https://gitlab.example.com/acme/front/web.git" in clone[0] and TOKEN not in " ".join(clone[0])
    assert clone[1]["GIT_CONFIG_KEY_0"] == "http.https://gitlab.example.com/.extraheader"
    assert db.get_release(rid)["message"] == "from gitlab"
    deployer.gitlab = lambda: None
    rid, ok = deploy_once(env, SHA1)
    assert not ok and "GitLab not connected" in deployer.log_path("web", rid).read_text()


@pytest.fixture
def api(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    with TestClient(app) as client:
        csrf = {"X-CSRF-Token": client.post("/api/login", json={"username": "admin", "password": PASSWORD}).json()["csrf"]}
        yield client, csrf, db, app


def connect(client, csrf, monkeypatch):
    monkeypatch.setattr(gitlab.GitLab, "whoami", lambda self: {"username": "root", "name": "Administrator", "version": "17.5.1"})
    return client.put("/api/gitlab", headers=csrf, json={"url": "https://gitlab.example.com", "token": TOKEN, "password": PASSWORD})


def test_connect_webhook_deploy_and_merge_request_preview(api, monkeypatch):
    client, csrf, db, app = api
    assert client.get("/api/gitlab").json()["connected"] is False
    assert client.put("/api/gitlab", headers=csrf, json={"url": "https://gitlab.example.com", "token": TOKEN, "password": "x"}).status_code == 403
    r = connect(client, csrf, monkeypatch)
    assert r.status_code == 200 and r.json()["user"]["username"] == "root"
    assert TOKEN not in json.dumps(dict(db.list_settings() if hasattr(db, "list_settings") else {}))
    assert db.get_setting(gitlab.S_TOKEN) and TOKEN not in db.get_setting(gitlab.S_TOKEN)  # kept encrypted
    status = client.get("/api/gitlab").json()
    assert status["connected"] and status["webhook_url"].endswith("/api/gitlab/webhook") and TOKEN not in json.dumps(status)
    assert not auth.api_token_allowed("/api/gitlab") and not auth.api_token_allowed("/api/gitlab/repos")

    # an app from GitLab: its webhook is added on the project
    hooks = []
    monkeypatch.setattr(gitlab.GitLab, "ensure_hook", lambda self, repo, url, key: hooks.append((repo, url, key)) or True)
    enqueued = []
    app.state.deployer.enqueue = lambda app_id, trigger, actor, sha=None, **kw: enqueued.append((app_id, trigger, actor, sha)) or 1
    r = client.post("/api/apps", headers=csrf, json={"id": "web", "repo": "acme/front/web", "branch": "main", "source": "gitlab",
                                                     "domain": "web.example.com", "type": "static"})
    assert r.status_code == 200, r.text
    assert db.get_app("web")["source"] == "gitlab" and r.json()["webhook"] == {"ok": True, "added": True}
    secret = hooks[0][2]
    assert hooks[0][0] == "acme/front/web" and hooks[0][1].endswith("/api/gitlab/webhook")

    # the webhook: the token is checked, a push deploys, a replay is refused
    push = {"ref": "refs/heads/main", "after": "a" * 40, "user_username": "ann",
            "project": {"path_with_namespace": "acme/front/web"}, "commits": []}
    head = {"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": secret, "X-Gitlab-Event-UUID": "u1"}
    assert client.post("/api/gitlab/webhook", json=push, headers={**head, "X-Gitlab-Token": "nope"}).status_code == 401
    out = client.post("/api/gitlab/webhook", json=push, headers=head).json()
    assert out["deployed"] == ["web"] and enqueued[-1] == ("web", "push", "gitlab:ann", "a" * 40)
    assert client.post("/api/gitlab/webhook", json=push, headers=head).status_code == 409
    # a GitHub push of a repository with the same name does not deploy the GitLab app
    assert db.apps_for_push("acme/front/web", "main") == [] and len(db.apps_for_push("acme/front/web", "main", source="gitlab")) == 1

    # a merge request: a preview of its branch, named after it, from GitLab too
    client.put("/api/apps/web/previews", headers=csrf, json={"enabled": True})
    comments = []
    monkeypatch.setattr(gitlab.GitLab, "comment", lambda self, inst, repo, n, text: comments.append((repo, n, text)))
    mr = {"project": {"path_with_namespace": "acme/front/web"}, "user": {"username": "bob"},
          "object_attributes": {"iid": 5, "action": "open", "source_branch": "menu", "target_branch": "main",
                                "source_project_id": 1, "target_project_id": 1, "last_commit": {"id": "b" * 40}}}
    out = client.post("/api/gitlab/webhook", json=mr, headers={**head, "X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Event-UUID": "u2"}).json()
    assert out.get("previews"), out
    preview = db.get_app(out["previews"][0])
    assert preview["source"] == "gitlab" and preview["branch"] == "menu" and "MR !5" in preview["name"]
    assert comments and comments[0][1] == 5


def test_mirror_a_github_repository_into_gitlab(api, monkeypatch):
    client, csrf, db, app = api
    connect(client, csrf, monkeypatch)
    db.create_app("site", name="site", repo="acme/site", branch="main", installation_id=7, type="static", mode="managed")
    assert client.put("/api/apps/site/mirror", headers=csrf, json={"project": "mirrors/site"}).status_code == 400  # no GitHub App
    monkeypatch.setattr(gitlab.GitLab, "ensure_project", lambda self, path, d="": {"full_name": path, "created": True})

    class GH:
        def token(self, inst):
            return "ghs_x"

        def installation_for(self, repo):
            return 7
    mirror = app.state.mirror
    mirror.github = lambda: GH()
    ran = []
    app.state.host.stream_hooks.append(lambda argv, cwd, env: ran.append((argv, env)))
    app.state.host._commands = {**app.state.host._commands, "*": {"returncode": 0}}
    r = client.put("/api/apps/site/mirror", headers=csrf, json={"project": "mirrors/site"})
    assert r.status_code == 200 and r.json()["mirror"]["project"] == "mirrors/site"
    out = client.post("/api/apps/site/mirror/sync", headers=csrf).json()
    assert out["ok"], out
    fetch = next(a for a in ran if "fetch" in a[0])
    push = next(a for a in ran if "push" in a[0])
    assert "https://github.com/acme/site.git" in fetch[0] and "https://gitlab.example.com/mirrors/site.git" in push[0]
    assert all("ghs_x" not in " ".join(a) and TOKEN not in " ".join(a) for a, _ in ran)  # tokens via the environment
    assert push[1]["GIT_CONFIG_KEY_0"].startswith("http.https://gitlab.example.com")
    assert out["mirror"]["error"] is None and out["mirror"]["at"]
    assert client.put("/api/apps/site/mirror", headers=csrf, json={"project": None}).json() == {"mirror": None}


def test_the_cms_edits_a_site_deployed_from_gitlab(tmp_path):
    from test_cms import SCHEMA, FakeGitHub, login

    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    db.create_app("site", name="Client site", repo="acme/front/site", branch="main", installation_id=None, type="static",
                  domain="client.example.com", auto_deploy=0, source="gitlab")
    app = create_app(host, Config(cookie_secure=False), db, background=False)
    repo = FakeGitHub({"mas.cms.json": json.dumps(SCHEMA).encode(), "content/home.json": b'{"title": "Hello"}\n'})
    repo.source = "gitlab"
    repo.can_write = lambda name: name == "acme/front/site"
    app.state.cms.github = lambda: None  # no GitHub App on this server at all
    app.state.deployer.gitlab = lambda: repo
    app.state.cms.deployer.enqueue = lambda app_id, trigger, actor, sha=None: 42
    client = TestClient(app)
    csrf = {"x-csrf-token": login(client)}
    assert [s["id"] for s in client.get("/api/cms/sites").json()] == ["site"]
    assert client.get("/api/apps/site/cms").json()["can_write"] is True
    page = client.get("/api/cms/sites/site").json()["pages"][0]
    assert page["values"]["title"] == "Hello"
    r = client.put("/api/cms/sites/site/pages/home", json={"values": {**page["values"], "title": "Bonjour"}, "sha": page["sha"]},
                   headers=csrf)
    assert r.status_code == 200, r.text
    assert json.loads(repo.files["content/home.json"])["title"] == "Bonjour"
    # the GitLab answers become the CMS's own messages
    from mas.cms import CmsConflict, CmsManager
    assert isinstance(CmsManager._github_error(gitlab.GitLabError("main moved since it was read: read it again")), CmsConflict)
    assert "Developer role" in str(CmsManager._github_error(gitlab.GitLabError("GitLab API POST /x: HTTP 403 Forbidden")))
    app.state.deployer.gitlab = lambda: None
    assert "GitLab is not connected" in client.get("/api/cms/sites/site").json()["detail"]


def test_each_release_reports_its_status_on_the_commit(env, api, monkeypatch):  # noqa: F811
    # the deployer calls its hooks once a release is over, live or failed
    deployer, db = env["deployer"], env["db"]
    seen = []
    deployer.after_release.append(lambda app, release, live: seen.append((app["id"], release["status"], live)))
    rid, ok = deploy_once(env, SHA1)
    import time as _t
    for _ in range(50):
        if seen:
            break
        _t.sleep(0.02)
    assert ok and seen == [("web", "live", True)]

    # the hook of Mas posts it on the commit: a GitLab commit status for a GitLab app
    client, csrf, adb, app = api
    statuses = []
    monkeypatch.setattr(gitlab.GitLab, "create_check_run",
                        lambda self, inst, repo, sha, name, conclusion, title, summary: statuses.append((repo, sha, name, conclusion, title)))
    connect(client, csrf, monkeypatch)
    adb.create_app("shop", name="shop", repo="acme/shop", branch="main", type="static", mode="managed",
                   domain="shop.example.com", source="gitlab")
    hook = app.state.deployer.after_release[-1]
    target = adb.get_app("shop")
    hook(target, {"sha": "a" * 40, "status": "live", "trigger": "push"}, True)
    hook(target, {"sha": "b" * 40, "status": "failed", "trigger": "push", "error": "npm run build exited with 1"}, False)
    hook(target, {"sha": "c" * 40, "status": "live", "trigger": "rollback"}, True)  # a rollback says nothing
    assert statuses == [("acme/shop", "a" * 40, "Mas deploy", "success", "Live at https://shop.example.com"),
                        ("acme/shop", "b" * 40, "Mas deploy", "failure", "Deployment failed: npm run build exited with 1")]
