"""Variables detected from .env.example / .env.sample when creating an app."""

from __future__ import annotations

import pytest
import server
from fastapi.testclient import TestClient

from mas import auth
from mas.api import create_app
from mas.config import Config
from mas.db import Database
from mas.envdetect import detect_env, parse_env_example
from mas.models import ServiceType
from mas.host import FixtureHost


def by_key(text: str):
    variables, ignored = parse_env_example(text)
    return {v.key: v for v in variables}, ignored


def test_values_quotes_and_blank_lines():
    found, _ = by_key(
        "\n# Database\nDATABASE_URL=postgres://localhost/app\n\n"
        "NAME=\"two words\"\nSINGLE='it works'\nTRAILING=value # inline comment\nexport EXPORTED=1\nEMPTY=\n"
    )
    assert found["DATABASE_URL"].value == "postgres://localhost/app"
    assert found["NAME"].value == "two words" and found["SINGLE"].value == "it works"
    assert found["TRAILING"].value == "value" and found["EXPORTED"].value == "1"
    assert found["EMPTY"].required and found["EMPTY"].value == ""
    assert not found["DATABASE_URL"].required and not found["DATABASE_URL"].optional


def test_commented_variable_is_optional_and_comments_describe():
    found, _ = by_key(
        "# Public URL of the site.\n# Used in emails.\nSITE_URL=https://example.com\n\n"
        "# Enable verbose logs\n# LOG_LEVEL=debug\n# see the docs for more\n# e.g. a=b\nOTHER=1\n"
    )
    assert found["SITE_URL"].description == "Public URL of the site. Used in emails."
    log = found["LOG_LEVEL"]
    assert log.optional and not log.required and log.value == "" and log.example == "debug"
    assert log.description == "Enable verbose logs"
    assert "a" not in found  # prose with an "=" is not a variable
    assert found["OTHER"].description == "see the docs for more e.g. a=b"


def test_blank_line_ends_a_description():
    found, _ = by_key("# Section header\n\nAPI_URL=https://x\n")
    assert found["API_URL"].description == ""


@pytest.mark.parametrize("value", ["", "change-me", "changeme", "<your-token>", "<...>", "your-api-key", "xxx", "..."])
def test_empty_and_placeholder_values_must_be_typed(value):
    found, _ = by_key(f"SOME_URL={value}\n")
    assert found["SOME_URL"].required and found["SOME_URL"].value == "" and found["SOME_URL"].example == value


def test_placeholder_in_a_commented_variable_stays_optional():
    found, _ = by_key("# STRIPE_URL=<url>\n")
    assert found["STRIPE_URL"].optional and not found["STRIPE_URL"].required


def test_duplicates():
    found, _ = by_key("A=1\nB=2\nA=3\n# B=9\n# C=1\nC=2\n# D=5\n# D=6\n")
    assert list(found) == ["A", "B", "C", "D"]  # first position kept
    assert found["A"].value == "3"  # last active definition wins
    assert found["B"].value == "2"  # a commented duplicate never overrides
    assert found["C"].value == "2" and not found["C"].optional  # an active line beats a commented one
    assert found["D"].optional and found["D"].example == "5"


def test_reserved_keys_are_ignored_even_when_commented():
    found, ignored = by_key("PORT=3000\nHOST=0.0.0.0\n# HOSTNAME=x\nNODE_ENV=production\nAPI_URL=https://x\nPORT=1\n")
    assert list(found) == ["API_URL"]
    assert ignored == ["PORT", "HOST", "HOSTNAME", "NODE_ENV"]
    assert detect_env(b"PORT=3000\n")["env"] == []


def test_lookalike_keys_are_not_reserved():
    found, ignored = by_key("IMAP_PORT=993\nSMTP_HOST=mail.example.com\n")
    assert set(found) == {"IMAP_PORT", "SMTP_HOST"} and ignored == []


def test_secret_fields():
    found, _ = by_key("DB_PASSWORD=\nAPI_TOKEN=\nJWT_SECRET=\nSTRIPE_KEY=\nAUTH_PASS=x\nSITE_URL=\n")
    assert [k for k, v in found.items() if v.secret] == ["DB_PASSWORD", "API_TOKEN", "JWT_SECRET", "STRIPE_KEY", "AUTH_PASS"]


def test_bytes_payload_is_tolerant():
    assert detect_env(None) == {"env": [], "env_ignored": []}
    assert detect_env(b"\xef\xbb\xbfA=1\n\xff\xfe\nB=2")["env"][0]["key"] == "A"  # BOM, invalid bytes
    assert detect_env(b"A=1\n" * 100000)["env"][0]["value"] == "1"  # bounded input


# -- API ----------------------------------------------------------------------


class FakeApp:
    files: dict[str, bytes] = {}

    def __init__(self, app_id, pem):
        pass

    def detect_type(self, installation_id, repo, ref):
        return ServiceType.NODE

    def read_file(self, installation_id, repo, path, ref):
        data = self.files.get(path)
        return (data, "sha") if data is not None else (None, None)


@pytest.fixture
def api(tmp_path, monkeypatch):
    host = FixtureHost(server.build(tmp_path / "srv"))
    host._commands["*"] = {"returncode": 0}
    db = Database(tmp_path / "mas.db")
    db.create_user("admin", auth.hash_password("correct horse battery"))
    config = Config(cookie_secure=False, github_app_id=1, secret_key_path="/etc/mas/secret.key")
    host.write_text(config.github_private_key_path, "pem")
    monkeypatch.setattr("mas.api.GitHubApp", FakeApp)
    FakeApp.files = {}
    app = create_app(host, config, db, background=False)
    app.state.deployer.enqueue = lambda *a, **k: 1
    client = TestClient(app)
    csrf = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).json()["csrf"]
    return client, db, {"X-CSRF-Token": csrf}


DETECT = "/api/github/detect?repo=acme/web&installation_id=1&ref=main"


def test_detect_reads_env_example_then_sample(api):
    client, *_ = api
    assert client.get(DETECT).json()["env"] == []  # no file: nothing to ask
    FakeApp.files = {".env.sample": b"SAMPLE_KEY=\nPORT=3000\n"}
    data = client.get(DETECT).json()
    assert data["env_file"] == ".env.sample" and [v["key"] for v in data["env"]] == ["SAMPLE_KEY"]
    assert data["env_ignored"] == ["PORT"] and data["env"][0]["secret"]
    FakeApp.files[".env.example"] = b"EXAMPLE_URL=https://x\n"
    data = client.get(DETECT).json()
    assert data["env_file"] == ".env.example" and [v["key"] for v in data["env"]] == ["EXAMPLE_URL"]


def test_create_refuses_an_empty_required_variable_and_stores_encrypted(api):
    client, db, h = api
    body = {"id": "web", "repo": "acme/web", "branch": "main", "installation_id": 9, "type": "nextjs",
            "env": "DB_PASSWORD=\"s3cret\"\nEMPTY=\n", "required_env": ["DB_PASSWORD", "EMPTY"]}
    r = client.post("/api/apps", json=body, headers=h)
    assert r.status_code == 400 and "EMPTY" in r.json()["detail"] and "DB_PASSWORD" not in r.json()["detail"]
    assert db.get_app("web") is None
    r = client.post("/api/apps", json={**body, "env": "DB_PASSWORD=\"s3cret\"\nEMPTY=ok\n"}, headers=h)
    assert r.status_code == 200, r.text
    assert "s3cret" not in (db.get_app("web")["env"] or "")  # encrypted at rest
    assert client.get("/api/apps/web/env").json()["text"] == "DB_PASSWORD=s3cret\nEMPTY=ok\n"
