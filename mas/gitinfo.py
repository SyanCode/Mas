"""Read git metadata straight from ``.git`` (no ``git`` binary, no writes)."""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from mas.host import Host
from mas.models import GitCommit

MAX_PARENT_LEVELS = 4
_SECTION_RE = re.compile(r'^\s*\[\s*([^\]\s"]+)(?:\s+"([^"]*)")?\s*\]\s*$')
_SIGNATURE_RE = re.compile(r"^(.*?) <[^>]*> (\d+) ([+-]\d{4})$")


@dataclass
class GitInfo:
    remote: str | None
    branch: str | None
    commit: GitCommit | None


def find_git_dir(host: Host, path: str) -> str | None:
    """Look for ``.git`` in ``path`` and a few parents (e.g. a PHP ``public/`` root)."""
    current = PurePosixPath(path)
    for _ in range(MAX_PARENT_LEVELS + 1):
        candidate = str(current / ".git")
        if host.is_dir(candidate):
            return candidate
        if host.is_file(candidate):  # worktree / submodule: "gitdir: <path>"
            text = (host.read_text(candidate) or "").strip()
            if text.startswith("gitdir:"):
                target = PurePosixPath(text[len("gitdir:"):].strip())
                return str(target if target.is_absolute() else current / target)
        if current.parent == current:
            break
        current = current.parent
    return None


def strip_credentials(url: str) -> str:
    """Never expose ``https://user:token@host/...``."""
    return re.sub(r"^([a-z+]+://)[^/@]+@", r"\1", url)


def parse_remote(config_text: str) -> str | None:
    remotes: dict[str, str] = {}
    section: tuple[str, str | None] | None = None
    for line in config_text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = (m.group(1).lower(), m.group(2))
            continue
        key, sep, value = line.partition("=")
        if sep and section and section[0] == "remote" and key.strip() == "url":
            remotes.setdefault(section[1] or "", value.strip())
    url = remotes.get("origin") or next(iter(remotes.values()), None)
    return strip_credentials(url) if url else None


def _resolve_ref(host: Host, git_dir: str, ref: str) -> str | None:
    loose = (host.read_text(f"{git_dir}/{ref}") or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", loose):
        return loose
    for line in (host.read_text(f"{git_dir}/packed-refs") or "").splitlines():
        sha, _, name = line.partition(" ")
        if name.strip() == ref and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    return None


def _read_commit(host: Host, git_dir: str, sha: str) -> GitCommit:
    raw = host.read_bytes(f"{git_dir}/objects/{sha[:2]}/{sha[2:]}")
    if raw is None:  # packed object: we only know the sha
        return GitCommit(sha=sha)
    try:
        data = zlib.decompress(raw)
    except zlib.error:
        return GitCommit(sha=sha)
    header, _, body = data.partition(b"\0")
    if not header.startswith(b"commit "):
        return GitCommit(sha=sha)
    text = body.decode("utf-8", "replace")
    headers, _, message = text.partition("\n\n")
    commit = GitCommit(sha=sha, subject=message.strip().splitlines()[0] if message.strip() else None)
    for line in headers.splitlines():
        if line.startswith("author "):
            m = _SIGNATURE_RE.match(line[len("author "):])
            if m:
                commit.author = m.group(1)
                tz = m.group(3)
                offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5]))
                tzinfo = timezone(offset if tz[0] == "+" else -offset)
                commit.date = datetime.fromtimestamp(int(m.group(2)), tzinfo)
    return commit


def read_git(host: Host, path: str | None) -> GitInfo | None:
    if not path:
        return None
    git_dir = find_git_dir(host, path)
    if git_dir is None:
        return None
    remote = parse_remote(host.read_text(f"{git_dir}/config") or "")
    head = (host.read_text(f"{git_dir}/HEAD") or "").strip()
    branch, sha = None, None
    if head.startswith("ref:"):
        ref = head[len("ref:"):].strip()
        branch = ref.removeprefix("refs/heads/")
        sha = _resolve_ref(host, git_dir, ref)
    elif re.fullmatch(r"[0-9a-f]{40}", head):
        sha = head  # detached HEAD
    commit = _read_commit(host, git_dir, sha) if sha else None
    return GitInfo(remote, branch, commit)
