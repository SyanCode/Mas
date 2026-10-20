from __future__ import annotations

import json

from mas.detect_type import detect_type
from mas.gitinfo import parse_remote, read_git, strip_credentials
from mas.host import Host
from mas.models import ServiceType as T


def _host(tmp_path, files: dict[str, str]) -> Host:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return Host(tmp_path)


def test_rules_in_order(tmp_path):
    h = _host(tmp_path, {
        "laravel/public/index.php": "",
        "laravel/package.json": json.dumps({"dependencies": {"next": "1"}}),
        "next/package.json": json.dumps({"devDependencies": {"next": "15"}}),
        "node/package.json": json.dumps({"dependencies": {"express": "4"}}),
        "py/pyproject.toml": "",
        "py2/requirements.txt": "",
        "site/index.html": "",
        "broken/package.json": "{nope",
    })
    assert detect_type(h, "/laravel/public") == T.PHP
    assert detect_type(h, "/laravel", fastcgi=True) == T.PHP
    assert detect_type(h, "/next") == T.NEXTJS
    assert detect_type(h, "/node") == T.NODE
    assert detect_type(h, "/broken") == T.NODE
    assert detect_type(h, "/py") == T.PYTHON
    assert detect_type(h, "/py2") == T.PYTHON
    assert detect_type(h, "/site", static_root=True) == T.STATIC
    assert detect_type(h, "/site", static_root=True, proxy=True) == T.UNKNOWN
    assert detect_type(h, None) == T.UNKNOWN


def test_cmdline_hint_is_last_resort(tmp_path):
    h = Host(tmp_path)
    assert detect_type(h, None, cmdline=["/usr/bin/python3", "-m", "http.server"]) == T.PYTHON
    assert detect_type(h, None, cmdline=["next-server (v15.0.0)"]) == T.NEXTJS
    assert detect_type(h, None, cmdline=["/usr/sbin/mariadbd"]) == T.UNKNOWN


def test_git_credentials_are_stripped():
    assert strip_credentials("https://user:tok@github.com/a/b.git") == "https://github.com/a/b.git"
    assert strip_credentials("git@github.com:a/b.git") == "git@github.com:a/b.git"
    assert parse_remote('[remote "upstream"]\n\turl = https://x/y.git\n') == "https://x/y.git"


def test_read_git_from_fixture(host):
    info = read_git(host, "/var/www/blog/public")  # repo is one level up
    assert info.remote == "https://github.com/acme/blog.git"
    assert info.branch == "main"
    assert info.commit.subject == "Fix pagination on archive pages"
    assert info.commit.author == "Alice Martin"
    assert info.commit.date.utcoffset().total_seconds() == 7200


def test_read_git_packed_refs(tmp_path):
    sha = "a" * 40
    h = _host(tmp_path, {
        "repo/.git/HEAD": "ref: refs/heads/dev\n",
        "repo/.git/packed-refs": f"# pack-refs with: peeled\n{sha} refs/heads/dev\n",
        "repo/.git/config": "",
    })
    info = read_git(h, "/repo")
    assert info.branch == "dev" and info.commit.sha == sha and info.commit.subject is None
    assert info.remote is None
