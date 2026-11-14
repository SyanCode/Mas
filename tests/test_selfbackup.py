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
