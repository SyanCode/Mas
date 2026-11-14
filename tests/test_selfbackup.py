"""Backup and restore of Mas itself."""

from __future__ import annotations

import io
import tarfile

import pytest

from mas import selfbackup
from mas.config import Config
from mas.db import Database


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(selfbackup, "CONFIG_PATH", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text("[server]\nport = 7070\n")
    (tmp_path / "secret.key").write_text("key-1")
    config = Config(db_path=str(tmp_path / "mas.db"), secret_key_path=str(tmp_path / "secret.key"),
                    github_private_key_path=str(tmp_path / "missing.pem"))
    db = Database(config.db_path)
    db.set_setting("hello", "world")
    return config, db, tmp_path


def test_backup_then_restore(cfg):
    config, db, tmp = cfg
    archive = selfbackup.create(config, tmp / "out")
    assert (archive.stat().st_mode & 0o777) == 0o600
    info = selfbackup.inspect(archive)
    assert info["files"] == ["config.toml", "secret.key"]  # the absent GitHub key is skipped
    db.set_setting("hello", "changed")
    (tmp / "secret.key").write_text("key-2")
    done = selfbackup.restore(config, archive)
    assert config.db_path in done
    assert Database(config.db_path).get_setting("hello") == "world"
    assert (tmp / "secret.key").read_text() == "key-1"
    assert (tmp / "secret.key.before-restore").read_text() == "key-2"


def test_keeps_only_the_latest(cfg):
    config, _, tmp = cfg
    for i in range(4):
        selfbackup.create(config, tmp / "out", keep=2, now=1_700_000_000 + i)
    assert len(selfbackup.list_backups(tmp / "out")) == 2


def test_refuses_foreign_or_unsafe_archives(cfg, tmp_path):
    config, *_ = cfg
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../../etc/passwd")
        info.size = 1
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(selfbackup.SelfBackupError):
        selfbackup.restore(config, evil)
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not an archive")
    with pytest.raises(selfbackup.SelfBackupError):
        selfbackup.inspect(junk)


def test_nightly_runs_once_in_the_window_and_can_be_turned_off(cfg):
    from datetime import datetime
    config, db, tmp = cfg
    night = datetime(2026, 11, 14, 3, 30).timestamp()
    day = datetime(2026, 11, 14, 15, 0).timestamp()
    assert selfbackup.nightly(db, config, day, tmp / "n") is None  # not at 3 p.m.
    first = selfbackup.nightly(db, config, night, tmp / "n")
    assert first and first.exists()
    assert selfbackup.nightly(db, config, night + 600, tmp / "n") is None  # once per night
    db.set_setting("selfbackup:last", "0")
    db.set_setting(selfbackup.AUTO, "0")
    assert selfbackup.nightly(db, config, night, tmp / "n") is None


def test_api_lists_and_runs_but_never_downloads(tmp_path, monkeypatch):
    from tests.test_notify import make
    monkeypatch.setattr(selfbackup, "BACKUP_DIR", str(tmp_path / "bk"))
    client, db, n, hooks, h = make(tmp_path)
    got = client.get("/api/self-backup").json()
    assert got["auto"] is True and got["backups"] == []
    assert client.put("/api/self-backup", headers=h, json={"auto": False}).json()["auto"] is False
    from mas import auth
    assert not auth.api_token_allowed("/api/self-backup")
