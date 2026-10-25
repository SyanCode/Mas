"""Projects of several repositories (mas.project.json), frozen deployments and folders."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost
from mas.models import ServiceType
from mas.projects import ProjectError, deploy_order, dump_manifest, parse_manifest, project_env

SHA1 = "1" * 40
SHA2 = "2" * 40

KLARFI = {
    "name": "Klarfi",
    "env": {"NEXT_PUBLIC_BRAND": "Klarfi"},
    "apps": [
        {"id": "klarfi-landing", "repo": "SyanCode/klarfi-landing", "domain": "Klarfi.com",
         "depends_on": ["klarfi-dashboard"],
         "env": {"NEXT_PUBLIC_APP_URL": "${apps.klarfi-dashboard.url}",
                 "DASHBOARD_API": "${apps.klarfi-dashboard.internal_url}/api"}},
        {"id": "klarfi-dashboard", "repo": "SyanCode/klarfi-dashboard", "domain": "app.klarfi.com",
         "type": "nextjs"},
    ],
}


# -- the file ------------------------------------------------------------------------


def test_manifest_is_normalized_and_ordered():
    m = parse_manifest(json.dumps(KLARFI))
    assert m["id"] == "klarfi" and m["apps"][0]["domain"] == "klarfi.com"
    assert m["apps"][0]["branch"] is None and m["apps"][1]["type"] == "nextjs"
    assert deploy_order(m) == ["klarfi-dashboard", "klarfi-landing"]  # the dashboard first: the landing needs it
    again = parse_manifest(dump_manifest(m))
    assert again == m


def test_manifest_id_defaults_to_the_repository_name():
    m = parse_manifest({"name": "Solo", "apps": [{"repo": "acme/My_Site"}]})
    assert m["apps"][0]["id"] == "my-site"


@pytest.mark.parametrize("change, message", [
    ({"apps": []}, "no app"),
    ({"name": ""}, "name"),
    ({"apps": [{"repo": "nope"}]}, "invalid repo"),
    ({"apps": [{"repo": "a/b", "domain": "not a domain"}]}, "invalid domain"),
    ({"apps": [{"repo": "a/b", "type": "cobol"}]}, "invalid type"),
    ({"apps": [{"repo": "a/b", "secret": "x"}]}, "unknown field"),
    ({"apps": [{"repo": "a/b"}, {"repo": "c/b"}]}, "used twice"),
    ({"apps": [{"repo": "a/b", "env": {"bad name": "x"}}]}, "invalid variable name"),
    ({"apps": [{"repo": "a/b", "env": {"A": "x\ny"}}]}, "several lines"),
    ({"apps": [{"repo": "a/b", "depends_on": ["zz"]}]}, "unknown app"),
    ({"apps": [{"repo": "a/b", "env": {"A": "${apps.zz.url}"}}]}, "not in the project"),
    ({"apps": [{"id": "x", "repo": "a/x", "depends_on": ["y"]}, {"id": "y", "repo": "a/y", "depends_on": ["x"]}]},
     "loop"),
    ({"apps": [{"id": "x", "repo": "a/x", "domain": "a.com"}, {"id": "y", "repo": "a/y", "domain": "a.com"}]},
     "same domain"),
])
def test_manifest_errors(change, message):
    with pytest.raises(ProjectError, match=message):
        parse_manifest({"name": "P", "apps": [{"repo": "a/b"}], **change})


def test_project_env_resolves_the_other_apps(tmp_path):
    db = Database(tmp_path / "mas.db")
    m = parse_manifest(KLARFI)
    db.save_project("klarfi", "Klarfi", json.dumps(m), None)
    db.create_app("klarfi-dashboard", name="d", repo="SyanCode/klarfi-dashboard", branch="main", type="nextjs",
                  port=7101, domain="app.klarfi.com", project="klarfi")
    db.create_app("klarfi-landing", name="l", repo="SyanCode/klarfi-landing", branch="main", type="nextjs",
                  port=7100, domain="klarfi.com", project="klarfi")
    env = project_env(db, db.get_app("klarfi-landing"))
    assert env == {"NEXT_PUBLIC_BRAND": "Klarfi", "NEXT_PUBLIC_APP_URL": "https://app.klarfi.com",
                   "DASHBOARD_API": "http://127.0.0.1:7101/api"}
    assert project_env(db, db.get_app("klarfi-dashboard")) == {"NEXT_PUBLIC_BRAND": "Klarfi"}
    db.update_app("klarfi-dashboard", domain=None)
    assert project_env(db, db.get_app("klarfi-landing"))["NEXT_PUBLIC_APP_URL"] == "http://127.0.0.1:7101"


# -- API -------------------------------------------------------------------------------


class FakeGitHub:
    def __init__(self):
        self.files: dict[tuple[str, str], bytes] = {}
        self.commits: list = []

    def installation_for(self, repo):
        if repo.startswith("stranger/"):
            from mas.github import GitHubError
            raise GitHubError("HTTP 404")
        return 7

    def default_branch(self, installation_id, repo):
        return "main"

    def detect_type(self, installation_id, repo, ref):
        return ServiceType.NEXTJS

    def read_file(self, installation_id, repo, path, ref):
        content = self.files.get((repo, path))
        return (content, "blob") if content is not None else (None, None)

    def head(self, installation_id, repo, branch):
        return SHA1

    def commit_files(self, installation_id, repo, branch, files, message, parent):
        self.commits.append((repo, branch, files, message, parent))
        for path, content in files.items():
            self.files[(repo, path)] = content
        return SHA2


@pytest.fixture
def api(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands["*"] = {"returncode": 0}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    config = Config(cookie_secure=False, github_app_id=1, github_webhook_secret="hooksecret",
                    secret_key_path="/etc/mas/secret.key")
    app = create_app(host, config, db, background=False)
    queued: list = []
    app.state.deployer.enqueue = lambda app_id, trigger, actor, sha=None, rollback_to=None: \
        queued.append((app_id, trigger, sha)) or len(queued)
    gh = FakeGitHub()
    app.state.projects.github = lambda: gh
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    return client, db, queued, {"X-CSRF-Token": csrf}, gh


def push(client, repo, branch="main", sha=SHA1, files=()):
    body = {"ref": f"refs/heads/{branch}", "after": sha, "repository": {"full_name": repo},
            "pusher": {"name": "alice"}, "head_commit": {"message": "Fix the header\n\nbody", "author": {"name": "Al"}},
            "commits": [{"added": [], "modified": list(files)}, {"added": [], "modified": []}]}
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"hooksecret", raw, hashlib.sha256).hexdigest()
    return client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "push",
                                                                     "X-Hub-Signature-256": sig}).json()


def test_frozen_app_keeps_its_commits_waiting(api):
    client, db, queued, h, _ = api
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100)
    db.create_app("api", name="api", repo="acme/web", branch="main", type="nextjs", port=7101)
    r = client.post("/api/apps/freeze", json={"app_ids": ["api", "nope"], "frozen": True}, headers=h)
    assert r.json()["changed"] == ["api"]
    result = push(client, "acme/web")
    assert result["deployed"] == ["web"] and result["frozen"] == ["api"]
    assert queued == [("web", "push", SHA1)]
    push(client, "acme/web", sha=SHA2)
    pending = client.get("/api/apps/api").json()["pending"]
    assert pending["sha"] == SHA2 and pending["commits"] == 4 and pending["message"] == "Fix the header"
    assert client.get("/api/deploy/freeze").json() == {"frozen": False, "frozen_apps": ["api"], "waiting": ["api"]}
    # "Redeploy all" leaves the frozen app alone unless asked
    assert [q["app_id"] for q in client.post("/api/apps/deploy-all", headers=h).json()["queued"]] == ["web"]


def test_freeze_everything(api):
    client, db, queued, h, _ = api
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100)
    assert client.put("/api/deploy/freeze", json={"frozen": True}, headers=h).json()["frozen"]
    result = push(client, "acme/web")
    assert result["deployed"] == [] and result["frozen"] == ["web"] and queued == []
    client.put("/api/deploy/freeze", json={"frozen": False}, headers=h)
    assert push(client, "acme/web", sha=SHA2)["deployed"] == ["web"]


def test_pending_is_cleared_when_the_branch_is_built(tmp_path):
    from mas.deploy import Deployer
    db = Database(tmp_path / "mas.db")
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", auto_deploy=0,
                  pending=json.dumps({"sha": SHA1, "commits": 2}))
    deployer = Deployer(FixtureHost(server.build(tmp_path / "srv")), Config(), db, lambda: None)
    rollback = db.create_release("web", "rollback", "admin", SHA1)
    deployer._mark_live(db.get_app("web"), rollback, "/x")
    assert db.get_app("web")["pending"]  # a rollback does not build the waiting commits
    manual = db.create_release("web", "manual", "admin", SHA2)
    deployer._mark_live(db.get_app("web"), manual, "/y")
    assert db.get_app("web")["pending"] is None


def test_import_a_project_from_a_repository(api):
    client, db, queued, h, gh = api
    gh.files[("SyanCode/klarfi-landing", "mas.project.json")] = json.dumps(KLARFI).encode()
    source = {"repo": "SyanCode/klarfi-landing", "branch": "main", "installation_id": 7}
    read = client.post("/api/projects/read", json=source, headers=h).json()
    assert read["order"] == ["klarfi-dashboard", "klarfi-landing"] and not read["exists"]
    assert read["manifest"]["apps"][0]["branch"] == "main"  # the repository holding the file follows its branch
    assert all(not a["exists"] for a in read["apps"])

    r = client.post("/api/projects", json={"manifest": read["manifest"], "source": source}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] == ["klarfi-landing", "klarfi-dashboard"]
    assert queued == [("klarfi-dashboard", "create", None), ("klarfi-landing", "create", None)]
    landing = db.get_app("klarfi-landing")
    assert landing["project"] == "klarfi" and landing["domain"] == "klarfi.com" and landing["installation_id"] == 7
    assert client.get("/api/projects").json()[0]["source"] == source

    # a push changing the file updates the project; a new app waits to be created
    changed = {**KLARFI, "apps": [*KLARFI["apps"], {"id": "klarfi-api", "repo": "SyanCode/klarfi-api"}]}
    gh.files[("SyanCode/klarfi-landing", "mas.project.json")] = json.dumps(changed).encode()
    push(client, "SyanCode/klarfi-landing", files=["mas.project.json"])
    project = client.get("/api/projects/klarfi").json()
    assert [a["id"] for a in project["apps"]] == ["klarfi-landing", "klarfi-dashboard", "klarfi-api"]
    assert not project["apps"][2]["exists"] and db.get_app("klarfi-api") is None

    queued.clear()
    synced = client.post("/api/projects/klarfi/sync", headers=h).json()
    assert synced["created"] == ["klarfi-api"] and queued == [("klarfi-api", "create", None)]

    queued.clear()
    deployed = client.post("/api/projects/klarfi/deploy", json={}, headers=h).json()["queued"]
    assert [q["app_id"] for q in deployed] == ["klarfi-dashboard", "klarfi-landing", "klarfi-api"]


def test_project_refuses_conflicts_and_unknown_repositories(api):
    client, db, queued, h, _ = api
    db.create_app("klarfi-landing", name="l", repo="other/site", branch="main", type="static")
    r = client.post("/api/projects", json={"manifest": KLARFI}, headers=h)
    assert r.status_code == 409 and "another repository" in r.json()["detail"]
    stranger = {"name": "S", "apps": [{"repo": "stranger/app"}]}
    r = client.post("/api/projects", json={"manifest": stranger}, headers=h)
    assert r.status_code == 400 and "install the GitHub App" in r.json()["detail"]
    assert db.get_project("s") is None and db.get_app("app") is None  # nothing half-created
    assert client.post("/api/projects", json={"manifest": {"name": "x"}}, headers=h).status_code == 400


def test_compose_in_mas_then_export_to_a_repository(api):
    client, db, queued, h, gh = api
    db.create_app("front", name="f", repo="acme/front", branch="main", type="nextjs", port=7100, domain="a.com")
    db.create_app("back", name="b", repo="acme/back", branch="main", type="node", port=7101)
    manifest = {"name": "Acme", "apps": [{"id": "front", "repo": "acme/front", "env": {"API": "${apps.back.url}"}},
                                         {"id": "back", "repo": "acme/back"}]}
    preview = client.post("/api/projects/validate", json={"manifest": manifest}, headers=h).json()
    assert '"${apps.back.url}"' in preview["file"] and all(a["exists"] for a in preview["apps"])
    r = client.post("/api/projects", json={"manifest": manifest, "deploy": False}, headers=h)
    assert r.json()["created"] == [] and queued == []
    assert db.get_app("front")["project"] == "acme"

    exported = client.post("/api/projects/acme/export", json={"repo": "acme/front", "branch": "main",
                                                              "installation_id": 7}, headers=h).json()
    assert exported["sha"] == SHA2 and exported["source"]["repo"] == "acme/front"
    repo, branch, files, message, parent = gh.commits[0]
    assert (repo, branch, parent) == ("acme/front", "main", SHA1) and "mas.project.json" in files
    assert parse_manifest(files["mas.project.json"])["apps"][0]["env"] == {"API": "${apps.back.url}"}
    client.post("/api/projects/acme/export", json={"repo": "acme/front", "branch": "main", "installation_id": 7},
                headers=h)
    assert len(gh.commits) == 1  # same content: no empty commit

    # an app removed from the file leaves the project, the app stays
    alone = {"name": "Acme", "apps": [{"id": "front", "repo": "acme/front"}]}
    r = client.put("/api/projects/acme", json={"manifest": {**alone, "apps": [{**alone["apps"][0],
                                                                                "env": {"API": "${apps.back.url}"}}]},
                                               "deploy": False}, headers=h)
    assert r.status_code == 400  # a variable names an app that left the project
    client.put("/api/projects/acme", json={"manifest": alone, "deploy": False}, headers=h)
    assert db.get_app("back")["project"] is None and db.get_app("back") is not None
    assert client.delete("/api/projects/acme", headers=h).json()["ok"]
    assert db.get_app("front")["project"] is None


# -- folders ---------------------------------------------------------------------------


def test_folders(api):
    client, db, queued, h, _ = api
    db.create_app("web", name="web", repo="acme/web", branch="main", type="nextjs", port=7100)
    db.create_app("site", name="site", repo="acme/site", branch="main", type="static")
    klarfi = client.post("/api/folders", json={"name": " Klarfi ", "color": "indigo"}, headers=h).json()
    assert klarfi["name"] == "Klarfi"
    sub = client.post("/api/folders", json={"name": "Front", "parent_id": klarfi["id"]}, headers=h).json()
    assert client.post("/api/folders", json={"name": "x", "color": "plaid"}, headers=h).status_code == 400
    # no folder inside itself or its children
    assert client.patch(f"/api/folders/{klarfi['id']}", json={"parent_id": sub["id"]}, headers=h).status_code == 400

    items = [{"kind": "app", "id": "web"}, {"kind": "app", "id": "site"}, {"kind": "service", "id": "nginx:a.com"}]
    client.put("/api/folders/items", json={"items": items, "folder_id": klarfi["id"]}, headers=h)
    listed = client.get("/api/folders").json()
    assert {(i["kind"], i["item_id"]) for i in listed["items"]} == {("app", "web"), ("app", "site"),
                                                                     ("service", "nginx:a.com")}
    assert client.put("/api/folders/items", json={"items": [{"kind": "x", "id": "y"}], "folder_id": None},
                      headers=h).status_code == 400

    r = client.post(f"/api/folders/{klarfi['id']}/sort", json={"labels": {"nextjs": "Next.js"}}, headers=h).json()
    assert r["moved"] == 2  # the vanished service is left where it is
    names = {f["name"]: f for f in client.get("/api/folders").json()["folders"]}
    assert {"Klarfi", "Front", "Next.js", "static"} <= set(names)
    assert names["Next.js"]["parent_id"] == klarfi["id"]

    # deleting a folder moves what it holds up one level; deleting an app forgets its place
    client.delete(f"/api/folders/{names['Next.js']['id']}", headers=h)
    where = {i["item_id"]: i["folder_id"] for i in client.get("/api/folders").json()["items"]}
    assert where["web"] == klarfi["id"]
    db.delete_app("web")
    assert "web" not in {i["item_id"] for i in client.get("/api/folders").json()["items"]}
    client.delete(f"/api/folders/{klarfi['id']}", headers=h)
    left = client.get("/api/folders").json()
    assert names["Front"]["id"] in {f["id"] for f in left["folders"] if f["parent_id"] is None}
    where = {i["item_id"]: i["folder_id"] for i in left["items"]}
    assert where["site"] == names["static"]["id"]  # its sub-folder moved up with it
    client.put("/api/folders/items", json={"items": [{"kind": "service", "id": "nginx:a.com"}], "folder_id": None},
               headers=h)
    client.post("/api/folders", json={"name": "Top"}, headers=h)
    assert "nginx:a.com" not in {i["item_id"] for i in client.get("/api/folders").json()["items"]}
