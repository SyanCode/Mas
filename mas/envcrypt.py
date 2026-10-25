"""Encryption of app environment variables (Fernet, key in /etc/mas/secret.key)."""

from __future__ import annotations

import json
import re

from cryptography.fernet import Fernet, InvalidToken

from mas.host import Host

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvError(ValueError):
    pass


def load_fernet(host: Host, path: str) -> Fernet:
    key = (host.read_text(path) or "").strip()
    if not key:
        key = Fernet.generate_key().decode()
        host.write_text(path, key + "\n", mode=0o600)
    return Fernet(key.encode())


def encrypt_env(fernet: Fernet, env: dict[str, str]) -> str:
    return fernet.encrypt(json.dumps(env, sort_keys=True).encode()).decode()


def decrypt_env(fernet: Fernet, token: str | None) -> dict[str, str]:
    if not token:
        return {}
    try:
        return json.loads(fernet.decrypt(token.encode()))
    except (InvalidToken, ValueError) as exc:
        raise EnvError("cannot decrypt environment (secret key changed?)") from exc


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines (``#`` comments, optional quotes, ``export`` prefix)."""
    env: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not ENV_KEY_RE.fullmatch(key):
            raise EnvError(f"line {n}: expected KEY=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key] = value
    return env


def systemd_env_file(env: dict[str, str]) -> str:
    """EnvironmentFile= content: double-quoted values, \\ and " escaped."""
    lines = []
    for key, value in sorted(env.items()):
        if "\n" in value or "\r" in value:
            raise EnvError(f"{key}: multi-line values are not supported")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + ("\n" if lines else "")
