"""Connecting Claude by driving the CLI login (a fake CLI stands in for `claude setup-token`)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import server

from mas.claudelogin import ClaudeLogin, LoginError, clean
from mas.host import FixtureHost

FAKE = str(Path(__file__).parent / "fixtures" / "fake_claude.py")


@pytest.fixture
def login(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    return ClaudeLogin(host, str(tmp_path / "home"), override=FAKE), host


def test_link_then_code_gives_the_token(login):
    cl, _ = login
    assert cl.status()["installed"]
    url = cl.start()
    assert url.startswith("https://claude.ai/oauth/authorize") and "\x1b" not in url
    assert cl.status()["pending"]
    token = cl.finish("GOOD-CODE#1234")
    assert token == "sk-ant-oat01-" + "T" * 60
    assert not cl.status()["pending"]
    with pytest.raises(LoginError, match="no login is waiting"):
        cl.finish("GOOD-CODE#1234")


def test_wrong_code_and_bad_input(login):
    cl, _ = login
    cl.start()
    with pytest.raises(LoginError, match="did not accept"):
        cl.finish("WRONG-CODE")
    cl.start()
    with pytest.raises(LoginError, match="does not look like"):
        cl.finish("rm -rf /; $(x)")
    cl.cancel()
    assert not cl.status()["pending"]


def test_not_installed_and_install(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    cl = ClaudeLogin(host, str(tmp_path / "home"), override=str(tmp_path / "missing"))
    assert not cl.status()["installed"]
    with pytest.raises(LoginError, match="not installed"):
        cl.start()
    host._commands = {"systemd-run *npm install*": {"output": "added 1 package"}, **host._commands}
    host.exists = lambda p: p == "/usr/bin/npm"
    assert "added" in cl.install()
    assert any(c[:1] == ["systemd-run"] and "@anthropic-ai/claude-code" in c for c in host.executed)


def test_clean_strips_terminal_codes():
    assert clean(b"\x1b[1mhello\x1b[0m\r\n\x1b]0;title\x07x") == "hello\nx"
