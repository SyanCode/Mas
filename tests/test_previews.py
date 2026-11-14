"""Preview deployments: one copy of an app per pull request, removed when it closes."""

from __future__ import annotations

from tests.test_deploy import SHA1, SHA2, api, signed  # noqa: F401 - the api fixture

HEADERS = {"X-GitHub-Event": "pull_request"}


def pr_event(action="opened", number=7, head_repo="acme/web", branch="feature-x", sha=SHA1, draft=False):
    return {"action": action, "repository": {"full_name": "acme/web"},
            "pull_request": {"number": number, "draft": draft, "user": {"login": "alice"}, "base": {"ref": "main"},
                             "head": {"ref": branch, "sha": sha, "repo": {"full_name": head_repo}}}}


def send(client, body):
    raw, sig = signed(body)
    return client.post("/api/github/webhook", content=raw, headers={**HEADERS, "X-Hub-Signature-256": sig}).json()


def setup(api, enabled=True):  # noqa: F811
    client, db, queued, h = api
    removed = []
    client.app.state.deployer.remove_app = lambda app_id, purge_files=False: (removed.append(app_id), db.delete_app(app_id), [])[2]
    r = client.post("/api/apps", json={"id": "web", "repo": "acme/web", "branch": "main", "installation_id": 9,
                                       "domain": "web.example.com", "type": "static", "env": "SECRET=prod\n"}, headers=h)
    assert r.status_code == 200, r.text
    queued.clear()
    if enabled:
        assert client.put("/api/apps/web/previews", json={"enabled": True, "max": 2}, headers=h).status_code == 200
    return client, db, queued, h, removed


def test_off_by_default(api):  # noqa: F811
    client, db, queued, h, _ = setup(api, enabled=False)
    assert send(client, pr_event())["previews"] == []
    assert db.get_app("web-pr7") is None and queued == []


def test_opened_creates_a_preview_without_the_secrets(api):  # noqa: F811
    client, db, queued, h, _ = setup(api)
    assert send(client, pr_event())["previews"] == ["web-pr7"]
    prev = db.get_app("web-pr7")
    assert prev["domain"] == "pr7.web.example.com" and prev["branch"] == "feature-x"
    assert prev["environment"] == "preview" and prev["env"] is None  # production variables are not copied
    assert queued == [("web-pr7", "create", SHA1)]
    assert client.get("/api/apps/web/previews").json()["previews"][0]["pr"] == 7
    # a push to the pull request's branch redeploys the preview through the usual path
    push = {"ref": "refs/heads/feature-x", "after": SHA2, "repository": {"full_name": "acme/web"}, "pusher": {"name": "alice"}}
    raw, sig = signed(push)
    client.post("/api/github/webhook", content=raw, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
    assert queued[-1] == ("web-pr7", "push", SHA2)


def test_variables_copied_only_when_asked(api):  # noqa: F811
    client, db, queued, h, _ = setup(api)
    client.put("/api/apps/web/previews", json={"enabled": True, "env": True, "max": 2}, headers=h)
    send(client, pr_event())
    assert db.get_app("web-pr7")["env"] == db.get_app("web")["env"]


def test_forks_drafts_and_limit(api):  # noqa: F811
    client, db, queued, h, _ = setup(api)
    assert send(client, pr_event(head_repo="mallory/web"))["ignored"] == "pull request from a fork"
    assert send(client, pr_event(draft=True))["ignored"] == "draft"
    assert db.get_app("web-pr7") is None
    send(client, pr_event(number=1, branch="a"))
    send(client, pr_event(number=2, branch="b"))
    assert send(client, pr_event(number=3, branch="c"))["previews"] == []  # max 2
    assert db.get_app("web-pr3") is None


def test_closing_removes_the_preview_and_deleting_the_app_too(api):  # noqa: F811
    client, db, queued, h, removed = setup(api)
    send(client, pr_event(number=1, branch="a"))
    send(client, pr_event(number=2, branch="b"))
    assert send(client, pr_event("closed", number=1))["removed"] == ["web-pr1"]
    assert removed == ["web-pr1"] and db.get_app("web-pr1") is None
    assert client.delete("/api/apps/web?purge=true", headers=h).status_code == 200
    assert "web-pr2" in removed


def test_never_touches_an_app_that_has_the_preview_id(api):  # noqa: F811
    client, db, queued, h, _ = setup(api)
    db.create_app("web-pr7", name="mine", repo="acme/other", branch="main", type="static")
    assert send(client, pr_event())["previews"] == []
    assert queued == [] and db.get_app("web-pr7")["repo"] == "acme/other"
