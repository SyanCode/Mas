"""The Mas API called in-process with an API token: what the MCP tools and the robot act through."""

from __future__ import annotations

from typing import Any


class MasError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Mas answered {status}: {detail}")
        self.status = status
        self.detail = detail


class MasClient:
    """The Mas API, called in-process (no network) with the caller's token."""

    def __init__(self, app, token: str, real_ip: str | None) -> None:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "mas-mcp"}
        if real_ip:  # trusted from 127.0.0.1: rate limits apply to the real caller
            headers["X-Real-IP"] = real_ip
        import httpx2  # only the robot and the MCP endpoint call the API this way
        self._http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app, client=("127.0.0.1", 0)),
                                        base_url="http://mas.internal", headers=headers, timeout=60)

    async def __aenter__(self) -> MasClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      json: Any = None, text: bool = False) -> Any:
        r = await self._http.request(method, path, params=params, json=json)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise MasError(r.status_code, str(detail)[:2000])
        return r.text if text else r.json()

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def patch(self, path: str, **kw: Any) -> Any:
        return await self.request("PATCH", path, **kw)
