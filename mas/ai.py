"""AI accounts: several providers, several accounts each, tried in the order of priority.

An account is a Claude Code token or API key (Anthropic), an OpenAI key (Codex and GPT models, Responses API),
or any OpenAI-compatible endpoint (Gemini, Mistral, OpenRouter, DeepSeek, a local Ollama…). The list is ordered:
Mas always starts with the first account that is usable. When it hits its limit (rate or usage limit, no credit left)
it is put aside until the limit resets and the next account takes over, in the middle of a task if need be; as soon
as the first one is back it is used again. A refused key is disabled until someone replaces it.

Everything speaks one neutral format (Anthropic-like): messages made of ``text``, ``tool_use`` and ``tool_result``
blocks. The OpenAI adapters translate it. Secrets are kept encrypted (mas.envcrypt) and never leave the server.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from mas.config import Config
from mas.db import Database
from mas.envcrypt import EnvError, load_fernet
from mas.host import Host

log = logging.getLogger(__name__)

PROVIDERS = {
    "anthropic": {"label": "Claude (Anthropic)", "model": "claude-sonnet-5-5", "url": "https://api.anthropic.com/v1/messages"},
    "openai": {"label": "Codex / GPT (OpenAI)", "model": "gpt-5-codex", "url": "https://api.openai.com/v1/responses"},
    "openai_compat": {"label": "OpenAI-compatible", "model": "", "url": ""},
}
CLAUDE_CODE_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
DEFAULT_COOLDOWN = 1800  # a limit with no stated reset: try the account again in half an hour
MAX_COOLDOWN = 8 * 3600
DOWN_COOLDOWN = 60  # server errors, no network: a minute
SECRET_RE = re.compile(r"^[A-Za-z0-9_.:\-]{16,600}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{2,100}$")


class AiError(Exception):
    pass


@dataclass
class Reply:
    """What a model answered, in the neutral format."""
    blocks: list[dict] = field(default_factory=list)  # {"type": "text", "text"} | {"type": "tool_use", "id", "name", "input"}
    stop: str = "end"  # end | tool_use | length
    account: str = ""
    tokens: int = 0

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.blocks if b["type"] == "text")

    @property
    def tool_calls(self) -> list[dict]:
        return [b for b in self.blocks if b["type"] == "tool_use"]


class LimitReached(AiError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Refused(AiError):  # the key is wrong, revoked or not allowed
    pass


class Unavailable(AiError):  # the provider is down or unreachable
    pass


def token_kind(provider: str, secret: str) -> str:
    if provider == "anthropic":
        return "claude-code" if secret.startswith("sk-ant-oat") else "api"
    return "api"


# -- adapters --------------------------------------------------------------------------------------


def _classify(code: int, body: str, headers) -> AiError:
    """Turn an HTTP failure into the right kind of error."""
    retry = None
    for name in ("retry-after", "x-ratelimit-reset-requests", "anthropic-ratelimit-unified-reset"):
        value = headers.get(name) if headers else None
        if value:
            try:
                retry = float(value) if float(value) < 1e8 else float(value) - time.time()  # seconds, or an epoch
            except ValueError:
                pass
            break
    low = body.lower()
    if code == 429 or code == 402 or "insufficient_quota" in low or "usage limit" in low or "credit balance" in low:
        return LimitReached(f"limit reached ({code})", retry)
    if code in (401, 403):
        return Refused(f"the key was refused ({code})")
    if code >= 500 or code == 529:
        return Unavailable(f"the provider answered {code}")
    return AiError(f"the provider answered {code}: {body[:200]}")


def _post(opener, url: str, headers: dict, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"content-type": "application/json",
                                 "user-agent": "mas", **headers}, method="POST")
    try:
        with opener(req, timeout=timeout) as resp:  # noqa: S310 - https, set by an administrator
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        raise _classify(exc.code, body, exc.headers) from None
    except (OSError, ValueError) as exc:
        raise Unavailable(f"cannot reach the provider: {exc}") from None


def call_anthropic(opener, acct: dict, secret: str, system: str, messages: list[dict], tools: list[dict],
                   max_tokens: int, timeout: float) -> Reply:
    headers = {"anthropic-version": "2023-06-01"}
    blocks = [{"type": "text", "text": system}]
    if token_kind("anthropic", secret) == "claude-code":  # a Claude Code token: bearer + beta flag + its own introduction
        headers.update({"authorization": f"Bearer {secret}", "anthropic-beta": "oauth-2025-04-20"})
        blocks.insert(0, {"type": "text", "text": CLAUDE_CODE_PREFIX})
    else:
        headers["x-api-key"] = secret
    payload = {"model": acct["model"], "max_tokens": max_tokens, "system": blocks, "messages": messages}
    if tools:
        payload["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tools]
    data = _post(opener, acct["base_url"] or PROVIDERS["anthropic"]["url"], headers, payload, timeout)
    out = []
    for b in data.get("content", []):
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "tool_use":
            out.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}})
    usage = data.get("usage") or {}
    stop = "tool_use" if data.get("stop_reason") == "tool_use" else "length" if data.get("stop_reason") == "max_tokens" else "end"
    return Reply(out, stop, tokens=int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)))


def _openai_items(messages: list[dict]) -> list[dict]:
    """Neutral messages → Responses API input items."""
    items: list[dict] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for b in content:
            if b["type"] == "text":
                items.append({"role": m["role"], "content": b["text"]})
            elif b["type"] == "tool_use":
                items.append({"type": "function_call", "call_id": b["id"], "name": b["name"], "arguments": json.dumps(b["input"])})
            elif b["type"] == "tool_result":
                items.append({"type": "function_call_output", "call_id": b["tool_use_id"], "output": b["content"]})
    return items


def call_openai(opener, acct: dict, secret: str, system: str, messages: list[dict], tools: list[dict],
                max_tokens: int, timeout: float) -> Reply:
    payload = {"model": acct["model"], "instructions": system, "input": _openai_items(messages),
               "max_output_tokens": max_tokens, "store": False}
    if tools:
        payload["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                             "parameters": t["schema"]} for t in tools]
    data = _post(opener, acct["base_url"] or PROVIDERS["openai"]["url"], {"authorization": f"Bearer {secret}"}, payload, timeout)
    out = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            text = "".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text")
            if text:
                out.append({"type": "text", "text": text})
        elif item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except ValueError:
                args = {}
            out.append({"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": args if isinstance(args, dict) else {}})
    usage = data.get("usage") or {}
    stop = "tool_use" if any(b["type"] == "tool_use" for b in out) else "length" if (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens" else "end"
    return Reply(out, stop, tokens=int(usage.get("total_tokens", 0)))


def _chat_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        text = "".join(b["text"] for b in content if b["type"] == "text")
        calls = [b for b in content if b["type"] == "tool_use"]
        if calls:
            out.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}} for b in calls]})
        elif text and not any(b["type"] == "tool_result" for b in content):
            out.append({"role": m["role"], "content": text})
        for b in content:
            if b["type"] == "tool_result":
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
    return out


def call_compat(opener, acct: dict, secret: str, system: str, messages: list[dict], tools: list[dict],
                max_tokens: int, timeout: float) -> Reply:
    if not acct["base_url"]:
        raise AiError("an OpenAI-compatible account needs its base URL")
    payload = {"model": acct["model"], "messages": _chat_messages(system, messages), "max_tokens": max_tokens}
    if tools:
        payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["schema"]}} for t in tools]
    base = acct["base_url"].rstrip("/")
    data = _post(opener, base if base.endswith("/chat/completions") else base + "/chat/completions",
                 {"authorization": f"Bearer {secret}"}, payload, timeout)
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    out = [{"type": "text", "text": msg["content"]}] if msg.get("content") else []
    for call in msg.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        out.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": args if isinstance(args, dict) else {}})
    stop = "tool_use" if msg.get("tool_calls") else "length" if choice.get("finish_reason") == "length" else "end"
    return Reply(out, stop, tokens=int((data.get("usage") or {}).get("total_tokens", 0)))


ADAPTERS = {"anthropic": call_anthropic, "openai": call_openai, "openai_compat": call_compat}


# -- the pool ----------------------------------------------------------------------------------------


class AiPool:
    def __init__(self, host: Host, config: Config, db: Database) -> None:
        self.host, self.config, self.db = host, config, db
        self.opener = urllib.request.urlopen  # replaced in tests
        self.now = time.time  # replaced in tests

    # secrets

    def _fernet(self):
        return load_fernet(self.host, self.config.secret_key_path)

    def _secret(self, acct: dict) -> str:
        try:
            return self._fernet().decrypt(acct["secret"].encode()).decode()
        except Exception as exc:  # noqa: BLE001
            raise EnvError("cannot decrypt the key (secret key changed?)") from exc

    # accounts

    def public(self, acct: dict) -> dict:
        now = self.now()
        try:
            secret = self._secret(acct)
        except EnvError:
            secret = ""
        waiting = acct["cooldown_until"] and acct["cooldown_until"] > now
        return {"id": acct["id"], "name": acct["name"], "provider": acct["provider"], "model": acct["model"],
                "base_url": acct["base_url"], "kind": token_kind(acct["provider"], secret) if secret else None,
                "hint": f"…{secret[-4:]}" if secret else "", "enabled": bool(acct["enabled"]), "priority": acct["priority"],
                "limited_until": acct["cooldown_until"] if waiting else None, "error": acct["last_error"] or "",
                "last_used": acct["last_used"], "uses": acct["uses"]}

    def accounts(self) -> list[dict]:
        return [self.public(a) for a in self.db.list_ai_accounts()]

    def add(self, name: str, provider: str, secret: str, model: str = "", base_url: str = "") -> dict:
        secret = secret.strip()
        if provider not in PROVIDERS:
            raise AiError("unknown provider")
        if not SECRET_RE.fullmatch(secret):
            raise AiError("this does not look like a key or a token")
        if provider == "anthropic" and not secret.startswith("sk-ant-"):
            raise AiError("a Claude key or token starts with sk-ant- (run `claude setup-token`, or create an API key in the Anthropic console)")
        model = model.strip() or PROVIDERS[provider]["model"]
        if not MODEL_RE.fullmatch(model):
            raise AiError("a model name is needed" if not model else "invalid model name")
        base_url = base_url.strip()
        if base_url and not base_url.startswith("https://"):
            raise AiError("the base URL must start with https://")
        if provider == "openai_compat" and not base_url:
            raise AiError("an OpenAI-compatible account needs its base URL (https://…/v1)")
        name = name.strip()[:60] or PROVIDERS[provider]["label"]
        sealed = self._fernet().encrypt(secret.encode()).decode()
        return self.public(self.db.add_ai_account(name, provider, sealed, model, base_url))

    def update(self, account_id: int, **fields) -> dict:
        allowed = {}
        if fields.get("name") is not None:
            allowed["name"] = fields["name"].strip()[:60]
        if fields.get("model") is not None:
            if not MODEL_RE.fullmatch(fields["model"].strip()):
                raise AiError("invalid model name")
            allowed["model"] = fields["model"].strip()
        if fields.get("enabled") is not None:
            allowed["enabled"] = 1 if fields["enabled"] else 0
            if fields["enabled"]:
                allowed.update(cooldown_until=None, last_error=None)
        if fields.get("secret"):
            secret = fields["secret"].strip()
            if not SECRET_RE.fullmatch(secret):
                raise AiError("this does not look like a key or a token")
            allowed.update(secret=self._fernet().encrypt(secret.encode()).decode(), cooldown_until=None, last_error=None, enabled=1)
        acct = self.db.update_ai_account(account_id, **allowed)
        if acct is None:
            raise AiError("unknown account")
        return self.public(acct)

    # asking

    def usable(self) -> list[dict]:
        now = self.now()
        return [a for a in self.db.list_ai_accounts() if a["enabled"] and not (a["cooldown_until"] and a["cooldown_until"] > now)]

    def configured(self) -> bool:
        return bool(self.db.list_ai_accounts())

    def ask(self, system: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4000,
            timeout: float = 120) -> Reply:
        """Ask the first usable account; when it is at its limit, the next one, and so on."""
        tried: list[str] = []
        for acct in self.usable():
            try:
                secret = self._secret(acct)
                reply = ADAPTERS[acct["provider"]](self.opener, acct, secret, system, messages, tools or [], max_tokens, timeout)
            except EnvError as exc:
                self.db.update_ai_account(acct["id"], enabled=0, last_error=str(exc))
                tried.append(f"{acct['name']}: {exc}")
            except LimitReached as exc:
                wait = min(MAX_COOLDOWN, max(60.0, exc.retry_after or DEFAULT_COOLDOWN))
                self.db.update_ai_account(acct["id"], cooldown_until=self.now() + wait, last_error=f"{exc}: waiting for the reset")
                tried.append(f"{acct['name']}: limit reached")
                log.info("AI account %s is at its limit, next one", acct["name"])
            except Refused as exc:
                self.db.update_ai_account(acct["id"], enabled=0, last_error=str(exc))
                tried.append(f"{acct['name']}: refused")
            except Unavailable as exc:
                self.db.update_ai_account(acct["id"], cooldown_until=self.now() + DOWN_COOLDOWN, last_error=str(exc))
                tried.append(f"{acct['name']}: unavailable")
            except AiError as exc:
                raise AiError(f"{acct['name']}: {exc}") from None
            else:
                self.db.update_ai_account(acct["id"], cooldown_until=None, last_error=None, used=self.now())
                reply.account = acct["name"]
                return reply
        if not tried:
            raise AiError("connect an AI account first (Settings → AI accounts)" if not self.configured()
                          else "every AI account is at its limit or disabled: Mas tries again when the first one resets")
        raise AiError("no AI account could answer (" + "; ".join(tried) + ")")
