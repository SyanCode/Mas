"""DNS provider settings: OVH credentials (encrypted) and this server's public IPs."""

from __future__ import annotations

import json

from mas.config import Config
from mas.db import Database
from mas.envcrypt import EnvError, decrypt_env, encrypt_env, load_fernet
from mas.host import Host
from mas.ovh import Opener, OvhClient, OvhCredentials

OVH_KEY = "ovh_credentials"
IPV4_KEY = "dns_ipv4"
IPV6_KEY = "dns_ipv6"


def save_ovh(host: Host, config: Config, db: Database, creds: OvhCredentials | None) -> None:
    if creds is None:
        db.set_setting(OVH_KEY, None)
        return
    fernet = load_fernet(host, config.secret_key_path)
    db.set_setting(OVH_KEY, encrypt_env(fernet, {
        "endpoint": creds.endpoint, "application_key": creds.application_key,
        "application_secret": creds.application_secret, "consumer_key": creds.consumer_key,
    }))


def load_ovh_credentials(host: Host, config: Config, db: Database) -> OvhCredentials | None:
    token = db.get_setting(OVH_KEY)
    if not token:
        return None
    try:
        data = decrypt_env(load_fernet(host, config.secret_key_path), token)
    except EnvError:
        return None
    return OvhCredentials(**data)


def ovh_client(host: Host, config: Config, db: Database, opener: Opener | None = None) -> OvhClient | None:
    creds = load_ovh_credentials(host, config, db)
    if creds is None:
        return None
    return OvhClient(creds, opener) if opener else OvhClient(creds)


def server_ips(db: Database, config: Config | None = None) -> tuple[str | None, str | None]:
    ipv4 = db.get_setting(IPV4_KEY) or (config.public_ip if config else None)
    return ipv4, db.get_setting(IPV6_KEY)


def ovh_account(db: Database) -> dict | None:
    raw = db.get_setting("ovh_account")
    return json.loads(raw) if raw else None
