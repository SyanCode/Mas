"""Routes of the file sharing (see mas.shares): administration under /api/shares, public pages under /d/<id>."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from mas import auth, shares
from mas.db import Database

# Whatever the file is, the browser downloads it and never renders it, and nothing it holds can run.
FILE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
PAGE_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


class ShareBody(BaseModel):
    name: str = Field(min_length=1, max_length=shares.MAX_NAME)
    size: int = Field(ge=1)
    password: str | None = Field(default=None, max_length=200)
    expires_hours: int | None = Field(default=None, ge=1, le=shares.MAX_EXPIRES_HOURS)
    max_downloads: int | None = Field(default=None, ge=1, le=shares.MAX_DOWNLOADS)


class ShareChange(BaseModel):
    password: str | None = Field(default=None, max_length=200)
    remove_password: bool = False
    expires_hours: int | None = Field(default=None, ge=1, le=shares.MAX_EXPIRES_HOURS)
    no_expiry: bool = False
    max_downloads: int | None = Field(default=None, ge=1, le=shares.MAX_DOWNLOADS)
    no_limit: bool = False


def register(app: FastAPI, *, db: Database, current_session: Callable, client_ip: Callable) -> None:
    limiter = auth.RateLimiter(max_hits=8, window_seconds=600)  # wrong passwords per address and share

    def public(share_id: str):
        row = shares._raw(db, share_id)
        return row if shares.alive(row) else None

    def gone() -> HTMLResponse:
        return HTMLResponse(shares.GONE_PAGE, status_code=404, headers=PAGE_HEADERS)

    def payload(item: dict) -> dict:
        return {**item, "url_path": f"/d/{item['id']}"}

    # -- administration ------------------------------------------------------------------------

    @app.get("/api/shares")
    def list_shares(_=Depends(current_session)) -> dict:
        free = shutil.disk_usage(shares._existing(shares.folder(db))).free
        return {"shares": [payload(s) for s in shares.listing(db)], "free": max(0, free - shares.RESERVE)}

    @app.post("/api/shares")
    def create_share(body: ShareBody, s=Depends(current_session)) -> dict:
        """Announce a file; its bytes follow with PUT /api/shares/<id>/content."""
        try:
            item = shares.create(db, s.username, body.name, body.size, body.password or None, body.expires_hours,
                                 body.max_downloads)
        except shares.ShareError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "share-create", True, detail=f"{item['name']} ({item['size']} bytes)")
        return payload(item)

    @app.put("/api/shares/{share_id}/content")
    async def upload(share_id: str, request: Request, s=Depends(current_session)) -> dict:
        """The file, as the raw body: written to disk as it arrives."""
        row = shares._raw(db, share_id)
        if row is None:
            raise HTTPException(404, "unknown share")
        if row["ready"]:
            raise HTTPException(409, "this share already has its file")
        part = shares.file_path(db, share_id).with_suffix(".part")
        part.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        received = 0
        out = await asyncio.to_thread(part.open, "wb")
        try:
            await asyncio.to_thread(part.chmod, 0o600)
            async for chunk in request.stream():
                received += len(chunk)
                if received > row["size"]:
                    raise HTTPException(413, "more bytes than announced")
                await asyncio.to_thread(out.write, chunk)
        except HTTPException:
            out.close()
            part.unlink(missing_ok=True)
            raise
        except Exception:  # connection dropped: nothing worth keeping, the person starts again
            out.close()
            part.unlink(missing_ok=True)
            raise HTTPException(400, "upload interrupted") from None
        out.close()
        try:
            item = shares.finish_upload(db, share_id, part, received)
        except shares.ShareError as exc:
            raise HTTPException(400, str(exc)) from None
        db.audit(s.username, "share-upload", True, detail=f"{item['name']} ({item['size']} bytes)")
        return payload(item)

    @app.patch("/api/shares/{share_id}")
    def change_share(share_id: str, body: ShareChange, s=Depends(current_session)) -> dict:
        try:
            item = shares.update(db, share_id, **body.model_dump())
        except shares.ShareError as exc:
            raise HTTPException(404 if "unknown" in str(exc) else 400, str(exc)) from None
        db.audit(s.username, "share-update", True, detail=item["name"])
        return payload(item)

    @app.delete("/api/shares/{share_id}")
    def delete_share(share_id: str, s=Depends(current_session)) -> dict:
        item = shares.get(db, share_id)
        if item is None or not shares.delete(db, share_id):
            raise HTTPException(404, "unknown share")
        db.audit(s.username, "share-delete", True, detail=item["name"])
        return {"ok": True}

    # -- public pages: the link ----------------------------------------------------------------

    @app.get("/d/{share_id}", include_in_schema=False)
    def download_page(share_id: str) -> Response:
        row = public(share_id)
        if row is None:
            return gone()
        return HTMLResponse(shares.page(row), headers=PAGE_HEADERS)

    @app.post("/d/{share_id}", include_in_schema=False)
    def unlock(share_id: str, request: Request, password: str = Form("", max_length=200)) -> Response:
        """The password form: the right one leads to the file through a link signed for an hour."""
        row = public(share_id)
        if row is None:
            return gone()
        key = f"{client_ip(request)}|{share_id}"
        if limiter.blocked(key):
            return HTMLResponse(shares.page(row, error="Too many attempts. Try again in a few minutes."),
                                status_code=429, headers={**PAGE_HEADERS, "Retry-After": "600"})
        if not row["password_hash"]:
            return RedirectResponse(f"/d/{share_id}", status_code=303)
        if not shares.check_password(row, password):
            limiter.hit(key)
            return HTMLResponse(shares.page(row, error="Wrong password."), status_code=403, headers=PAGE_HEADERS)
        # The page it answers carries the signed link: GET, so that it can be refreshed, copied, resumed.
        return HTMLResponse(shares.page(row, token=shares.make_link_token(db, row)), headers=PAGE_HEADERS)

    @app.api_route("/d/{share_id}/file", methods=["GET", "HEAD"], include_in_schema=False)
    def download(share_id: str, request: Request, t: str = Query("", max_length=200)) -> Response:
        row = public(share_id)
        if row is None:
            return Response("This link has expired or does not exist.", status_code=404, media_type="text/plain",
                            headers=FILE_HEADERS)
        if row["password_hash"]:
            key = f"{client_ip(request)}|{share_id}"
            allowed = bool(t) and shares.check_link_token(db, row, t)
            if not allowed:
                supplied = shares.basic_password(request.headers.get("authorization", ""))
                if supplied is not None:
                    if limiter.blocked(key):
                        return Response("Too many attempts.", status_code=429, media_type="text/plain",
                                        headers={**FILE_HEADERS, "Retry-After": "600"})
                    allowed = shares.check_password(row, supplied)
                    if not allowed:
                        limiter.hit(key)
            if not allowed:
                return Response("Password required.", status_code=401, media_type="text/plain",
                                headers={**FILE_HEADERS, "WWW-Authenticate": 'Basic realm="download", charset="UTF-8"'})
        path = shares.file_path(db, share_id)
        if not path.is_file():
            return Response("This link has expired or does not exist.", status_code=404, media_type="text/plain",
                            headers=FILE_HEADERS)
        # A resumed download (a range that does not start at 0) and a HEAD do not use up a download.
        rng = request.headers.get("range", "").replace(" ", "")
        first = request.method == "GET" and (not rng or rng.startswith("bytes=0-"))
        if first and not shares.count_download(db, share_id):
            return Response("This link has expired or does not exist.", status_code=404, media_type="text/plain",
                            headers=FILE_HEADERS)
        return FileResponse(path, media_type="application/octet-stream", filename=row["name"],
                            content_disposition_type="attachment", headers=FILE_HEADERS)
