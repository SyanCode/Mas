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
    assert token == "sk-ant-oat01-" + "T" * 30 + "-_" + "U" * 30
    assert not cl.status()["pending"]
    with pytest.raises(LoginError, match="no login is waiting"):
        cl.finish("GOOD-CODE#1234")


def test_wrong_code_and_bad_input(login):
    cl, _ = login
    cl.start()
    with pytest.raises(LoginError, match="did not give a token"):
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


def test_extract_token_waits_for_the_end_of_the_token():
    from mas.claudelogin import extract_token
    token = "sk-ant-oat01-" + "a" * 70
    assert extract_token("Your token:\n" + token[:30], False) is None  # still arriving
    assert extract_token("Your token:\n" + token[:40] + "\n" + token[40:], False) is None
    assert extract_token(f"x\n{token[:40]}\n{token[40:]}\n\nStore this token securely.", False) == token
    assert extract_token(f"{token} Store this", False) == token
    assert extract_token(token, True) == token  # the CLI exited
    assert extract_token("sk-ant-oat01-short\n\nx", False) is None


def test_flat_fallback_and_shape():
    from mas.claudelogin import extract_token, shape
    token = "sk-ant-oat01-" + "Qz9" * 30
    weird = f"Your token:\nsk-ant-\noat01-{token[13:50]}\n  │ {token[50:]} │\nStore this token securely."
    assert extract_token(weird, False) == token
    assert extract_token(f"{token}Storethistokensecurely.", False) == token
    out = shape(f"a\n{token}\nStore this")
    assert token not in out and "sk-ant-oat01-<" in out and "⏎" in out


def test_raw_window_shows_only_the_start_of_the_token():
    from mas.claudelogin import raw_window
    out = raw_window(b"\x1b[1Cyear):\x1b[1Csk-ant-oat01-SECRETSECRETSECRET")
    assert "sk-ant-oat01-S" in out and "SECRETSECRET" not in out and "\\x1b[1C" in out
