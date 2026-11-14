"""Profile of an account: full name, nickname, about and a picture."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from mas import auth
from mas.profile import ProfileError, clean_profile, parse_avatar, sniff
from tests.test_api import PASSWORD, env, login  # noqa: F401 - the env fixture

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def url(data: bytes, kind: str = "image/png") -> str:
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


def test_text_fields_are_cleaned_and_limited():
    assert clean_profile({"display_name": "  Syan   Code ", "nickname": "syan_01", "bio": "a\nb"}) == {
        "display_name": "Syan Code", "nickname": "syan_01", "bio": "a\nb"}
    assert clean_profile({"display_name": "line\nbreak"}) == {"display_name": "line break"}  # one line
    assert clean_profile({"display_name": "  ", "nickname": ""}) == {"display_name": None, "nickname": None}
    for bad in ({"display_name": "x" * 81}, {"nickname": "<script>"}, {"nickname": "a" * 33}, {"bio": "x" * 301},
                {"display_name": "evil‮txt"}, {"nickname": 5}):
        with pytest.raises(ProfileError):
            clean_profile(bad)


def test_pictures_are_sniffed_never_trusted():
    assert sniff(PNG) == "image/png" and sniff(JPEG) == "image/jpeg" and sniff(b"RIFF....WEBPxxxx") == "image/webp"
    assert parse_avatar(url(PNG)) == (PNG, "image/png")
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    for bad in (url(svg, "image/png"), url(PNG, "image/jpeg"), url(b"x" * 10), "data:image/svg+xml;base64,PHN2Zz4=",
                url(PNG + b"\x00" * (300 * 1024)), "not a data url", None, url(b"")[:-3] + "!!!"):
        with pytest.raises(ProfileError):
            parse_avatar(bad)


def test_profile_api_round_trip_and_session(env):  # noqa: F811
    client, db, uid, _ = env
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert client.get("/api/account/profile").json()["display_name"] is None
    assert client.put("/api/account/profile", headers=h, json={"nickname": "<b>"}).status_code == 400
    r = client.put("/api/account/profile", headers=h, json={"display_name": "Syan Code", "nickname": "syan", "bio": "Hello"})
    assert r.status_code == 200 and r.json()["nickname"] == "syan" and r.json()["has_avatar"] is False
    assert client.get("/api/account/avatar").status_code == 404
    put = client.put("/api/account/avatar", headers=h, json={"data_url": url(PNG)})
    assert put.status_code == 200 and put.json()["has_avatar"] and put.json()["avatar_v"] == 1
    got = client.get("/api/account/avatar")
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff" and "sandbox" in got.headers["content-security-policy"]
    session = client.get("/api/session").json()
    assert session["display_name"] == "Syan Code" and session["has_avatar"] and session["avatar_v"] == 1
    assert client.put("/api/account/avatar", headers=h, json={"data_url": "data:image/png;base64,AAAA"}).status_code == 400
    gone = client.delete("/api/account/avatar", headers=h).json()
    assert gone["has_avatar"] is False and gone["avatar_v"] == 2
    assert client.get("/api/account/avatar").status_code == 404
    assert client.put("/api/account/profile", headers={}, json={"bio": "x"}).status_code == 403  # CSRF


def test_who_sees_what(env):  # noqa: F811
    client, db, uid, _ = env
    db.create_user("eve", auth.hash_password(PASSWORD), role="viewer")
    eve = TestClient(client.app)
    token = eve.post("/api/login", json={"username": "eve", "password": PASSWORD}).json()["csrf"]
    he = {"X-CSRF-Token": token}
    # a read-only account keeps its own profile
    assert eve.put("/api/account/profile", headers=he, json={"display_name": "Eve"}).status_code == 200
    assert eve.put("/api/account/avatar", headers=he, json={"data_url": url(JPEG, "image/jpeg")}).status_code == 200
    eve_id = db.get_user("eve")["id"]
    # the administrator sees it in the list and can fetch the picture, a viewer never fetches another one
    csrf = login(client)
    listed = {u["username"]: u for u in client.get("/api/users").json()}
    assert listed["eve"]["display_name"] == "Eve" and listed["eve"]["has_avatar"] and "avatar" not in listed["eve"]
    assert client.get(f"/api/users/{eve_id}/avatar").content == JPEG
    assert eve.get("/api/users/1/avatar").status_code == 403
    # the administrator names an account but cannot change its picture
    named = client.put(f"/api/users/{eve_id}/profile", headers={"X-CSRF-Token": csrf}, json={"display_name": "Eve Smith", "bio": "ignored"})
    assert named.json()["display_name"] == "Eve Smith" and named.json()["bio"] is None
    # API tokens never reach any of it
    assert not auth.api_token_allowed("/api/account/avatar") and not auth.api_token_allowed("/api/users/1/avatar")


def test_list_queries_never_carry_the_pictures(env):  # noqa: F811
    client, db, uid, _ = env
    db.set_avatar(uid, PNG, "image/png")
    assert "avatar" not in dict(db.list_users()[0]).keys() and db.get_user("admin")["has_avatar"] == 1


def test_cli_profile(tmp_path):
    import server
    from typer.testing import CliRunner

    from mas.cli import app
    from mas.db import Database

    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password(PASSWORD))
    base = ["--fixture", str(server.build(tmp_path / "srv")), "--db", str(tmp_path / "mas.db")]
    run = CliRunner()
    out = run.invoke(app, [*base, "user", "profile", "admin", "--name", "Syan Code", "--nickname", "syan"])
    assert out.exit_code == 0 and "Syan Code" in out.output and "syan" in out.output, out.output
    assert db.get_user("admin")["display_name"] == "Syan Code"
    assert run.invoke(app, [*base, "user", "profile", "admin", "--nickname", "<bad>"]).exit_code != 0
    assert run.invoke(app, [*base, "user", "profile", "nobody"]).exit_code != 0
