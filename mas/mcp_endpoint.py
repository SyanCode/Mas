"""MCP endpoint (``/mcp``): lets Claude Code or any MCP client operate this server.

Off until an administrator turns it on (Settings → Claude). Each tool is a call to this very API,
made in-process with the caller's personal API token (``mas_pat_…``): the whitelisted actions,
``nginx -t`` before reloads, rollbacks, the audit log and the routes closed to tokens (accounts,
secrets) all apply. Mas contains no AI: this is only a protocol, like the HTTP API.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json

from mas import auth
from mas.masclient import MasClient, MasError  # noqa: F401 - imported from here by older code

SETTING = "mcp_enabled"
CLIENT_IP_HEADER = b"x-mas-mcp-client-ip"


def build_server(app, **kw):
    from mas.mcp_tools import build_server as build
    return build(app, **kw)


async def _send_json(send, status: int, body: dict, extra: list | None = None) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()),
        (b"cache-control", b"no-store"), *(extra or [])]})
    await send({"type": "http.response.body", "body": data})


class Endpoint:
    """ASGI app of ``/mcp``: off unless enabled, then a valid API token in the Authorization header.

    The MCP server (and its library) is only built on the first call once the endpoint is on, and its session manager
    then runs in a task of its own until Mas stops (``run`` in the lifespan): a server where Claude is never connected
    never loads it."""

    def __init__(self, app, db, limiter, trusted_proxies: set[str]) -> None:
        self.app, self.db, self.limiter, self.trusted = app, db, limiter, trusted_proxies
        self.server = None
        self.inner = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._start: asyncio.Event | None = None
        self._ready: concurrent.futures.Future | None = None  # thread-safe: a request may run in another loop

    def _build(self) -> None:
        from mcp.server.transport_security import TransportSecuritySettings
        self.server = build_server(self.app)
        self.inner = self.server.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True, json_response=True,
            # every request carries a token checked below: DNS rebinding protection (meant for
            # unauthenticated local servers) would only refuse the dashboard's own domain
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    async def run(self, stop: asyncio.Event) -> None:
        """Lives as long as Mas: waits for the first call, then builds the server and keeps its session manager on."""
        self._loop, self._start, self._ready = asyncio.get_running_loop(), asyncio.Event(), concurrent.futures.Future()
        waiter = asyncio.ensure_future(self._start.wait())
        ender = asyncio.ensure_future(stop.wait())
        await asyncio.wait({waiter, ender}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if stop.is_set():
            ender.cancel()
            return
        try:
            self._build()
            async with self.server.session_manager.run():
                self._ready.set_result(True)
                await ender
        except Exception as exc:  # noqa: BLE001 - the requests waiting must get an answer
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            if not self._ready.done():
                self._ready.set_exception(RuntimeError("Mas is stopping"))

    async def _inner(self):
        if self._ready is None or self._loop is None:  # no lifespan: nothing can keep the session manager on
            raise RuntimeError("the MCP endpoint is not running")
        if not self._ready.done():
            self._loop.call_soon_threadsafe(self._start.set)
        try:
            await asyncio.wrap_future(self._ready)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"the MCP endpoint could not start: {exc}") from None
        return self.inner

    async def __call__(self, scope, receive, send):
        if self.db.get_setting(SETTING) != "1":
            return await _send_json(send, 404, {"detail": "the MCP endpoint is off (Settings → Claude)"})
        headers = [(k, v) for k, v in scope["headers"] if k != CLIENT_IP_HEADER]
        h = dict(headers)
        peer = (scope.get("client") or ("unknown",))[0]
        ip = h[b"x-real-ip"].decode() if peer in self.trusted and h.get(b"x-real-ip") else peer
        authz = h.get(b"authorization", b"").decode()
        token = authz[7:].strip() if authz[:7].lower() == "bearer " else ""
        challenge = [(b"www-authenticate", b'Bearer realm="mas"')]
        if self.limiter.blocked(ip):
            return await _send_json(send, 429, {"detail": "too many attempts"})
        if not token.startswith(auth.API_TOKEN_PREFIX):
            return await _send_json(send, 401, {"detail": "a Mas API token is required"}, challenge)
        row = await asyncio.to_thread(self.db.use_api_token, auth.hash_token(token))
        if row is None:
            self.limiter.hit(ip)
            return await _send_json(send, 401, {"detail": "invalid API token"}, challenge)
        if row["role"] != "admin":
            return await _send_json(send, 403, {"detail": "administrators only"})
        scope = dict(scope, headers=[*headers, (CLIENT_IP_HEADER, ip.encode())])
        try:
            inner = await self._inner()
        except RuntimeError as exc:
            return await _send_json(send, 503, {"detail": str(exc)})
        return await inner(scope, receive, send)
