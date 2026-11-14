"""Backup and restore of Mas itself: its database, its secret key, its config and the GitHub App key.

A backup is ``/var/lib/mas/backups/mas/mas-<date>.tar.gz`` (mode 0600) holding ``mas.db`` (a consistent
snapshot taken with SQLite's backup API while Mas runs), ``files/<name>`` for each of ``config.toml``,
``secret.key`` and ``github-app.pem`` that exists, and ``manifest.json``. The archive contains the keys
that decrypt the stored secrets: keep it off the server too (``mas backup --output``).

Restoring puts the current files aside as ``<file>.before-restore`` first, and never writes outside the
paths of the configuration.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from mas import __version__
from mas.config import CONFIG_PATH, Config

BACKUP_DIR = "/var/lib/mas/backups/mas"
NAME_RE = re.compile(r"^mas-\d{8}-\d{6}\.tar\.gz$")
MAX_SIZE = 512 * 1024 * 1024


class SelfBackupError(Exception):
    pass


def _files(config: Config) -> dict[str, str]:
    """Archive name -> path on the server, for the files Mas needs besides its database."""
    return {
        "config.toml": CONFIG_PATH,
        "secret.key": config.secret_key_path,
        "github-app.pem": config.github_private_key_path,
    }


def _snapshot(db_path: str, target: str) -> None:
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def list_backups(directory: str | Path = BACKUP_DIR) -> list[dict]:
    path = Path(directory)
    if not path.is_dir():
        return []
    out = []
    for f in sorted(path.iterdir(), reverse=True):
        if NAME_RE.match(f.name):
            out.append({"name": f.name, "path": str(f), "size": f.stat().st_size, "modified": f.stat().st_mtime})
    return out


def create(config: Config, directory: str | Path | None = None, keep: int = 7, now: float | None = None) -> Path:
    """Write a new backup into ``directory`` (default ``BACKUP_DIR``) and prune the old ones."""
    if not os.path.exists(config.db_path):
        raise SelfBackupError(f"no database at {config.db_path}")
    target_dir = Path(directory or BACKUP_DIR)
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive = target_dir / f"mas-{stamp}.tar.gz"
    present = {name: p for name, p in _files(config).items() if os.path.isfile(p)}
    manifest = {"version": __version__, "created": stamp, "db": config.db_path, "files": sorted(present)}
    with tempfile.TemporaryDirectory(prefix="mas-backup-") as tmp:
        snap = os.path.join(tmp, "mas.db")
        _snapshot(config.db_path, snap)
        tmp_archive = archive.with_suffix(".tmp")
        fd = os.open(tmp_archive, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            tar.add(snap, arcname="mas.db")
            for name, path in present.items():
                tar.add(path, arcname=f"files/{name}")
            data = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            info.mtime = int(now or time.time())
            tar.addfile(info, io.BytesIO(data))
        os.replace(tmp_archive, archive)
    if keep > 0:
        for old in list_backups(target_dir)[keep:]:
            Path(old["path"]).unlink(missing_ok=True)
    return archive


def _read_archive(archive: Path, tmp: str) -> dict:
    """Extract only the expected members into ``tmp``; returns the manifest."""
    if not archive.is_file():
        raise SelfBackupError(f"{archive} is not a file")
    if archive.stat().st_size > MAX_SIZE:
        raise SelfBackupError("archive too large")
    allowed = {"mas.db", "manifest.json", *(f"files/{n}" for n in ("config.toml", "secret.key", "github-app.pem"))}
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name not in allowed or not member.isfile():
                    raise SelfBackupError(f"unexpected entry in the archive: {member.name!r}")
                (Path(tmp) / member.name).parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                with open(Path(tmp) / member.name, "wb") as out:
                    shutil.copyfileobj(src, out)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise SelfBackupError(f"cannot read the archive: {exc}") from exc
    try:
        manifest = json.loads((Path(tmp) / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise SelfBackupError("not a Mas backup (no manifest)") from exc
    if not (Path(tmp) / "mas.db").is_file():
        raise SelfBackupError("not a Mas backup (no database)")
    try:
        con = sqlite3.connect(os.path.join(tmp, "mas.db"))
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise SelfBackupError(f"the database in the archive is damaged: {exc}") from exc
    if ok != "ok":
        raise SelfBackupError("the database in the archive is damaged")
    return manifest


def inspect(archive: str | Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="mas-restore-") as tmp:
        return _read_archive(Path(archive), tmp)


def restore(config: Config, archive: str | Path) -> list[str]:
    """Put the database and files of a backup back. Returns what was restored."""
    done: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mas-restore-") as tmp:
        _read_archive(Path(archive), tmp)

        def place(src: str, dest: str, mode: int) -> None:
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if dest_path.exists():
                shutil.copy2(dest_path, f"{dest}.before-restore")
            staged = f"{dest}.restoring"
            shutil.copyfile(src, staged)
            os.chmod(staged, mode)
            os.replace(staged, dest)
            done.append(dest)

        place(os.path.join(tmp, "mas.db"), config.db_path, 0o600)
        for name, dest in _files(config).items():
            src = os.path.join(tmp, "files", name)
            if os.path.isfile(src):
                place(src, dest, 0o600)
        for suffix in ("-wal", "-shm"):  # leftovers of the replaced database
            Path(config.db_path + suffix).unlink(missing_ok=True)
    return done


# -- every night ---------------------------------------------------------------------------------------------

NIGHT = range(3, 5)  # hours (server time) of the nightly backup
DAY = 20 * 3600  # at most one per window
AUTO = "selfbackup:auto"  # setting: "0" turns the nightly backup off, on by default
LAST = "selfbackup:last"


def auto_enabled(db) -> bool:
    return db.get_setting(AUTO) != "0"


def nightly(db, config: Config, now: float | None = None, directory: str | Path | None = None, keep: int = 7) -> Path | None:
    """Back up once a night, between 3 and 5 a.m., unless it was turned off. Returns the archive or None."""
    now = now or time.time()
    if not auto_enabled(db) or datetime.fromtimestamp(now).hour not in NIGHT:
        return None
    if now - float(db.get_setting(LAST) or 0) < DAY:
        return None
    db.set_setting(LAST, str(now))
    try:
        path = create(config, directory, keep, now)
    except (SelfBackupError, OSError, sqlite3.Error) as exc:
        db.audit("selfbackup", "mas-backup", False, detail=str(exc)[:300])
        return None
    db.audit("selfbackup", "mas-backup", True, detail=f"{path.name} (nightly)")
    return path
