"""Copy of the backups of Mas to an S3-compatible storage."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from mas.offsite import OffsiteError, sign_v4
from tests.test_notify import make

BODY = {"enabled": True, "endpoint": "https://s3.fr-par.scw.cloud", "region": "fr-par", "bucket": "my-backups",
        "prefix": "mas/", "access_key": "SCWACCESS", "secret_key": "sup3r-s3cret"}


def test_signature_matches_the_aws_published_example():
    payload = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
    headers = {"date": "Fri, 24 May 2013 00:00:00 GMT", "host": "examplebucket.s3.amazonaws.com",
               "x-amz-content-sha256": payload, "x-amz-date": "20130524T000000Z",
               "x-amz-storage-class": "REDUCED_REDUNDANCY"}
    auth = sign_v4("PUT", "https://examplebucket.s3.amazonaws.com/test%24file.text", headers, payload,
                   "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                   datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert auth.endswith("Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")


def setup(tmp_path):
    client, db, n, hooks, h = make(tmp_path)
    sent = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    client.app.state.offsite.opener = lambda req, timeout=0: (sent.append(req), Resp())[1]
    return client, db, sent, h


def test_settings_are_validated_and_the_secret_never_comes_back(tmp_path):
    client, db, sent, h = setup(tmp_path)
    for bad in ({"endpoint": "http://insecure.example.com"}, {"region": "FR PAR"}, {"bucket": "UPPER"},
                {"access_key": ""}, {"prefix": "../x"}):
        r = client.put("/api/self-backup/remote", headers=h, json={**BODY, **bad})
        assert r.status_code == 400, bad
    r = client.put("/api/self-backup/remote", headers=h, json=BODY)
    assert r.status_code == 200 and r.json()["has_secret"] and "secret_key" not in r.json()
    assert "sup3r-s3cret" not in db.get_setting("selfbackup:remote")  # encrypted at rest
    kept = client.put("/api/self-backup/remote", headers=h, json={**BODY, "secret_key": ""})
    assert kept.json()["has_secret"]  # an empty field keeps the old key
    assert "sup3r-s3cret" not in client.get("/api/self-backup/remote").text


def test_test_and_nightly_upload(tmp_path):
    client, db, sent, h = setup(tmp_path)
    client.put("/api/self-backup/remote", headers=h, json=BODY)
    assert client.post("/api/self-backup/remote/test", headers=h).json()["key"] == "mas/mas-test.txt"
    req = sent[-1]
    assert req.full_url == "https://s3.fr-par.scw.cloud/my-backups/mas/mas-test.txt" and req.get_method() == "PUT"
    assert req.get_header("Authorization").startswith("AWS4-HMAC-SHA256 Credential=SCWACCESS/")
    assert "sup3r-s3cret" not in req.get_header("Authorization")
    archive = tmp_path / "mas-20261114-030000.tar.gz"
    archive.write_bytes(b"archive")
    assert client.app.state.offsite.upload(archive) == "mas/mas-20261114-030000.tar.gz"
    assert sent[-1].data == b"archive"


def test_upload_is_off_by_default_and_a_failure_never_raises(tmp_path):
    client, db, sent, h = setup(tmp_path)
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"x")
    offsite = client.app.state.offsite
    assert offsite.upload(archive) is None and sent == []
    client.put("/api/self-backup/remote", headers=h, json=BODY)

    def boom(req, timeout=0):
        raise OSError("down")
    offsite.opener = boom
    assert offsite.upload(archive) is None  # the local backup is what counts
    assert any(e["action"] == "mas-backup-offsite" and not e["ok"] for e in db.history(None, 20))
    with pytest.raises(OffsiteError):
        offsite.test()
