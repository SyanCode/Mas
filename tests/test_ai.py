"""AI accounts: encrypted, ordered by priority, the next one takes over when one hits its limit."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
import server

from mas.ai import AiError, AiPool, _chat_messages, _openai_items
from mas.config import Config
from mas.db import Database
from mas.host import FixtureHost

OAUTH = "sk-ant-oat01-" + "a" * 40
API = "sk-ant-api03-" + "b" * 40
OPENAI = "sk-proj-" + "c" * 40


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def anthropic_text(text):
    return Resp(json.dumps({"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                            "usage": {"input_tokens": 3, "output_tokens": 4}}).encode())


def http_error(url, code, headers=None):
    return urllib.error.HTTPError(url, code, "x", headers or {}, io.BytesIO(b"{}"))


@pytest.fixture
def pool(tmp_path):
    host = FixtureHost(server.build(tmp_path / "srv"))
    db = Database(tmp_path / "mas.db")
    p = AiPool(host, Config(), db)
    p.clock = [1000.0]
    p.now = lambda: p.clock[0]
    return p


def test_accounts_are_checked_encrypted_and_ordered(pool):
    with pytest.raises(AiError):
        pool.add("x", "anthropic", "not-a-claude-key-at-all-123456")
    with pytest.raises(AiError):
        pool.add("x", "openai_compat", OPENAI)  # no base URL
    with pytest.raises(AiError):
        pool.add("x", "openai_compat", OPENAI, "m", "http://insecure")
    a = pool.add("Pro 1", "anthropic", OAUTH)
    b = pool.add("Pro 2", "anthropic", API)
    c = pool.add("Codex", "openai", OPENAI)
    assert [x["priority"] for x in (a, b, c)] == [1, 2, 3]
    assert a["kind"] == "claude-code" and b["kind"] == "api" and a["hint"] == "…aaaa"
    assert OAUTH not in json.dumps(pool.accounts()) and OAUTH not in pool.db.list_ai_accounts()[0]["secret"]
    pool.db.move_ai_account(c["id"], -1)
    pool.db.move_ai_account(c["id"], -1)
    assert [x["name"] for x in pool.accounts()] == ["Codex", "Pro 1", "Pro 2"]


def test_the_next_account_takes_over_at_the_limit_and_the_first_comes_back(pool):
    pool.add("Pro 1", "anthropic", OAUTH)
    pool.add("Pro 2", "anthropic", API)
    calls = []

    def opener(req, timeout):
        who = "first" if req.get_header("Authorization") else "second"
        calls.append(who)
        if who == "first" and limited[0]:
            raise http_error(req.full_url, 429, {"retry-after": "3600"})
        return anthropic_text(who)
    limited = [True]
    pool.opener = opener
    assert pool.ask("s", [{"role": "user", "content": "hi"}]).text == "second"
    assert calls == ["first", "second"]
    first = pool.accounts()[0]
    assert first["limited_until"] == 1000.0 + 3600 and "limit" in first["error"]
    calls.clear()
    assert pool.ask("s", [{"role": "user", "content": "hi"}]).text == "second"
    assert calls == ["second"]  # the limited one is not asked again
    pool.clock[0] += 3601  # its limit reset
    limited[0] = False
    calls.clear()
    reply = pool.ask("s", [{"role": "user", "content": "hi"}])
    assert reply.text == "first" and reply.account == "Pro 1" and calls == ["first"]
    assert pool.accounts()[0]["limited_until"] is None


def test_a_refused_key_is_disabled_and_everything_down_is_reported(pool):
    pool.add("Pro 1", "anthropic", API)
    pool.opener = lambda req, timeout: (_ for _ in ()).throw(http_error(req.full_url, 401))
    with pytest.raises(AiError, match="refused"):
        pool.ask("s", [{"role": "user", "content": "x"}])
    assert pool.accounts()[0]["enabled"] is False
    with pytest.raises(AiError, match="limit or disabled"):
        pool.ask("s", [{"role": "user", "content": "x"}])
    pool.update(pool.accounts()[0]["id"], enabled=True)
    assert pool.accounts()[0]["enabled"] is True


def test_nothing_connected(pool):
    with pytest.raises(AiError, match="connect an AI account"):
        pool.ask("s", [{"role": "user", "content": "x"}])


def test_anthropic_request_shape(pool):
    pool.add("Pro", "anthropic", OAUTH)
    seen = []
    pool.opener = lambda req, timeout: seen.append(req) or Resp(json.dumps({"content": [
        {"type": "tool_use", "id": "t1", "name": "list_sites", "input": {}}], "stop_reason": "tool_use"}).encode())
    tools = [{"name": "list_sites", "description": "d", "schema": {"type": "object", "properties": {}}}]
    reply = pool.ask("sys", [{"role": "user", "content": "go"}], tools)
    body = json.loads(seen[0].data)
    assert reply.stop == "tool_use" and reply.tool_calls[0]["name"] == "list_sites"
    assert body["tools"][0]["input_schema"]["type"] == "object" and body["system"][0]["text"].startswith("You are Claude Code")
    assert seen[0].get_header("Anthropic-beta") == "oauth-2025-04-20"


def test_openai_responses_and_compatible_adapters(pool):
    pool.add("Codex", "openai", OPENAI)
    seen = []
    pool.opener = lambda req, timeout: seen.append(req) or Resp(json.dumps({"output": [
        {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
        {"type": "function_call", "call_id": "c1", "name": "repo_read", "arguments": "{\"path\": \"a\"}"}],
        "usage": {"total_tokens": 9}}).encode())
    reply = pool.ask("sys", [{"role": "user", "content": "go"}], [{"name": "repo_read", "description": "d", "schema": {"type": "object"}}])
    body = json.loads(seen[0].data)
    assert seen[0].full_url.endswith("/v1/responses") and body["instructions"] == "sys" and body["tools"][0]["type"] == "function"
    assert reply.text == "hello" and reply.tool_calls[0]["input"] == {"path": "a"} and reply.stop == "tool_use"
    pool.db.delete_ai_account(pool.accounts()[0]["id"])
    pool.add("Gemini", "openai_compat", "AIza" + "d" * 30, "gemini-2.5-pro", "https://generativelanguage.googleapis.com/v1beta/openai")
    pool.opener = lambda req, timeout: seen.append(req) or Resp(json.dumps({"choices": [{"message": {"content": "hi", "tool_calls": [
        {"id": "x", "function": {"name": "list_sites", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}).encode())
    reply = pool.ask("sys", [{"role": "user", "content": "go"}])
    assert seen[-1].full_url.endswith("/openai/chat/completions") and reply.tool_calls[0]["id"] == "x"


def test_history_translates_for_openai():
    history = [{"role": "user", "content": "go"},
               {"role": "assistant", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "1", "name": "n", "input": {"a": 1}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "done"}]}]
    items = _openai_items(history)
    assert items[1]["role"] == "assistant" and items[2]["type"] == "function_call" and items[3] == {
        "type": "function_call_output", "call_id": "1", "output": "done"}
    chat = _chat_messages("sys", history)
    assert chat[2]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}' and chat[3]["role"] == "tool"
