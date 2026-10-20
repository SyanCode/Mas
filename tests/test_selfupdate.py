"""self-update: new-version check and following the published branch, even after a force-push."""

from __future__ import annotations

import json
import subprocess

import pytest

from mas.config import Config
from mas.selfupdate import CHECK_TTL, UpdateError, local_head, update_available, update_checkout


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, content):
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git(work, "checkout", "-q", "-b", "main")
    commit(work, "a.txt", "a")
    git(work, "push", "-q", "origin", "main")
    src = tmp_path / "src"
    subprocess.run(["git", "clone", "-q", str(origin), str(src)], check=True, capture_output=True)
    return work, src


def test_local_head_loose_and_packed(repos):
    work, src = repos
    branch, sha = local_head(src)
    assert branch == "main" and sha == git(src, "rev-parse", "HEAD")
    git(src, "pack-refs", "--all")
    assert local_head(src) == ("main", sha)
    assert local_head(src / "missing") is None


def test_update_check_is_cached(repos, tmp_path):
    _, src = repos
    config = Config(src_dir=str(src), db_path=str(tmp_path / "data" / "mas.db"))
    (tmp_path / "data").mkdir()
    calls = []

    def fetch(sha, branch):
        calls.append((sha, branch))
        return 3

    assert update_available(config, now=1000, fetch=fetch) == 3
    assert update_available(config, now=1000 + CHECK_TTL - 1, fetch=fetch) == 3
    assert len(calls) == 1
    assert update_available(config, now=1000 + CHECK_TTL + 1, fetch=lambda s, b: 0) == 0
    cache = json.loads((tmp_path / "data" / "update-check.json").read_text())
    assert cache["behind"] == 0 and cache["sha"] == local_head(src)[1]

    def offline(sha, branch):
        raise OSError("no network")

    (tmp_path / "data" / "update-check.json").unlink()
    assert update_available(config, now=5000, fetch=offline) == 0


def test_checkout_follows_a_force_push(repos):
    work, src = repos
    commit(work, "b.txt", "b")
    git(work, "push", "-q", "origin", "main")
    update_checkout(src, log=lambda *_: None)
    assert (src / "b.txt").read_text() == "b"
    # rewrite history on the remote: a plain fast-forward pull would refuse
    git(work, "reset", "-q", "--hard", "HEAD~1")
    rewritten = commit(work, "c.txt", "c")
    git(work, "push", "-q", "--force", "origin", "main")
    update_checkout(src, log=lambda *_: None)
    assert git(src, "rev-parse", "HEAD") == rewritten and not (src / "b.txt").exists()


def test_checkout_refuses_local_changes(repos):
    work, src = repos
    (src / "a.txt").write_text("edited on the server")
    with pytest.raises(UpdateError, match="local changes"):
        update_checkout(src, log=lambda *_: None)
    assert (src / "a.txt").read_text() == "edited on the server"
