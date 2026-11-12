"""Undo a CMS change with a new commit, and refuse when later work would be lost."""

from __future__ import annotations

import json

from test_cms import PNG, SCHEMA, FakeGitHub, env, login  # noqa: F401

from mas.github import GitHubError

BASE = "/api/cms/sites/site/collections/articles/entries"


class HistoryGitHub(FakeGitHub):
    """A repository that remembers every commit, so a past state can be read and a commit described."""

    def __init__(self, files):
        super().__init__(files)
        self.snapshots = {"c0": dict(files)}
        self.parents = {}

    def read_file(self, installation_id, repo, path, ref):
        state = self.snapshots.get(ref, self.files)
        data = state.get(path)
        return (data, f"blob-{hash(data) & 0xffffff}") if data is not None else (None, None)

    def commit_files(self, installation_id, repo, branch, files, message, parent):
        before = dict(self.files)
        sha = super().commit_files(installation_id, repo, branch, files, message, parent)
        self.snapshots[sha] = dict(self.files)
        self.parents[sha] = (parent, before)
        return sha

    def get_commit(self, installation_id, repo, sha):
        if sha not in self.parents:
            raise GitHubError("GitHub API GET commits: HTTP 404 Not Found")
        parent, before = self.parents[sha]
        after = self.snapshots[sha]
        out = []
        for path in sorted(set(before) | set(after)):
            if before.get(path) == after.get(path):
                continue
            status = "added" if path not in before else "removed" if path not in after else "modified"
            out.append({"filename": path, "status": status, "sha": None if status == "removed" else f"blob-{hash(after[path]) & 0xffffff}"})
        return {"parents": [parent], "files": out, "truncated": False}


def setup(env_):
    client, db, gh = env_
    hist = HistoryGitHub(gh.files)
    client.app.state.cms.github = lambda: hist
    return client, db, hist, {"x-csrf-token": login(client)}


def test_undoing_a_new_entry_removes_it_and_the_index_follows(env):
    client, db, gh, h = setup(env)
    r = client.post(BASE, json={"values": {"title": "Oops", "date": "2026-01-01"}}, headers=h)
    assert r.status_code == 200 and "content/articles/oops.json" in gh.files
    changes = client.get("/api/cms/sites/site/changes").json()
    assert changes[0]["summary"].startswith("add Oops") and changes[0]["undoable"] and not changes[0]["reverted"]

    undo = client.post(f"/api/cms/sites/site/changes/{changes[0]['id']}/revert", headers=h)
    assert undo.status_code == 200, undo.text
    assert "content/articles/oops.json" not in gh.files
    assert "oops" not in gh.files.get("content/articles/index.json", b"").decode()  # the index did not exist before
    assert gh.commits[-1][0].startswith("cms(revert): undo add Oops")
    after = client.get("/api/cms/sites/site/changes").json()
    assert after[0]["scope"] == "cms(revert)" and not after[0]["undoable"] and after[1]["reverted"]
    assert client.post(f"/api/cms/sites/site/changes/{changes[0]['id']}/revert", headers=h).status_code == 409  # twice
    assert client.post(f"/api/cms/sites/site/changes/{after[0]['id']}/revert", headers=h).status_code == 400  # an undo
    assert any(e["action"] == "cms-revert" and e["ok"] for e in db.history(None, 20))


def test_undoing_an_edit_restores_the_previous_text_but_not_over_later_work(env):
    client, db, gh, h = setup(env)
    client.post(BASE, json={"values": {"title": "Post", "date": "2026-01-01", "body": "v1"}}, headers=h)
    entry = client.get(f"{BASE}/post").json()
    client.put(f"{BASE}/post", json={"values": {**entry["values"], "body": "v2"}, "sha": entry["sha"]}, headers=h)
    entry = client.get(f"{BASE}/post").json()
    client.put(f"{BASE}/post", json={"values": {**entry["values"], "body": "v3"}, "sha": entry["sha"]}, headers=h)
    edit1, edit2 = [c for c in client.get("/api/cms/sites/site/changes").json()][1], client.get("/api/cms/sites/site/changes").json()[0]

    # v2 was changed again by v3: undoing it would erase v3, so it is refused
    refused = client.post(f"/api/cms/sites/site/changes/{edit1['id']}/revert", headers=h)
    assert refused.status_code == 409 and "changed since" in refused.json()["detail"]
    # the latest change can be undone: back to v2
    assert client.post(f"/api/cms/sites/site/changes/{edit2['id']}/revert", headers=h).status_code == 200
    assert json.loads(gh.files["content/articles/post.json"])["body"] == "v2"


def test_undoing_a_deletion_brings_the_entry_back(env):
    client, db, gh, h = setup(env)
    client.post(BASE, json={"values": {"title": "Keep", "date": "2026-01-01"}}, headers=h)
    entry = client.get(f"{BASE}/keep").json()
    client.delete(f"{BASE}/keep", params={"sha": entry["sha"]}, headers=h)
    assert "content/articles/keep.json" not in gh.files
    deleted = client.get("/api/cms/sites/site/changes").json()[0]
    assert deleted["summary"].startswith("delete Keep")
    assert client.post(f"/api/cms/sites/site/changes/{deleted['id']}/revert", headers=h).status_code == 200
    assert json.loads(gh.files["content/articles/keep.json"])["title"] == "Keep"
    assert "keep" in gh.files["content/articles/index.json"].decode()


def test_access_is_confined_to_the_site(env):
    client, db, gh, h = setup(env)
    assert client.get("/api/cms/sites/nope/changes").status_code == 404
    assert client.post("/api/cms/sites/nope/changes/1/revert", headers=h).status_code == 404
    assert client.post("/api/cms/sites/site/changes/999/revert", headers=h).status_code == 400
