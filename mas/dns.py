"""Connected DNS providers (credentials encrypted at rest) and this server's public IPs."""

from __future__ import annotations

import json

from mas.config import Config
from mas.db import Database
from mas.dnsproviders import DnsError, Opener, Provider, make_provider
from mas.envcrypt import EnvError, decrypt_env, encrypt_env, load_fernet
from mas.host import Host

PROVIDERS_KEY = "dns_providers"
LEGACY_OVH_KEY = "ovh_credentials"  # before multi-provider support
IPV4_KEY = "dns_ipv4"
IPV6_KEY = "dns_ipv6"


def load_providers(host: Host, config: Config, db: Database) -> dict[str, dict]:
    """``{type: {"creds": {...}, "account": str}}``."""
    fernet = load_fernet(host, config.secret_key_path)
    token = db.get_setting(PROVIDERS_KEY)
    if token:
        try:
            return json.loads(decrypt_env(fernet, token).get("json", "{}"))
        except (EnvError, ValueError):
            return {}
    legacy = db.get_setting(LEGACY_OVH_KEY)
    if not legacy:
        return {}
    try:  # migrate the single OVH account of the previous version
        creds = decrypt_env(fernet, legacy)
    except EnvError:
        return {}
    account = json.loads(db.get_setting("ovh_account") or "{}").get("nichandle") or "OVH account"
    providers = {"ovh": {"creds": creds, "account": account}}
    save_providers(host, config, db, providers)
    db.set_setting(LEGACY_OVH_KEY, None)
    db.set_setting("ovh_account", None)
    return providers


def save_providers(host: Host, config: Config, db: Database, providers: dict[str, dict]) -> None:
    if not providers:
        db.set_setting(PROVIDERS_KEY, None)
        return
    fernet = load_fernet(host, config.secret_key_path)
    db.set_setting(PROVIDERS_KEY, encrypt_env(fernet, {"json": json.dumps(providers)}))


def provider_clients(host: Host, config: Config, db: Database, opener: Opener | None = None) -> list[Provider]:
    clients = []
    for ptype, entry in load_providers(host, config, db).items():
        try:
            clients.append(make_provider(ptype, entry["creds"], opener))
        except DnsError:
            continue
    return clients


def zone_index(host: Host, config: Config, db: Database, opener: Opener | None = None) -> tuple[dict[str, Provider],
                                                                                               list[str]]:
    """Map each zone to the provider that hosts it. Also returns per-provider errors."""
    index: dict[str, Provider] = {}
    errors = []
    for provider in provider_clients(host, config, db, opener):
        try:
            for zone in provider.zones():
                index.setdefault(zone, provider)
        except DnsError as exc:
            errors.append(f"{provider.label}: {exc}")
    return index, errors


def server_ips(db: Database, config: Config | None = None) -> tuple[str | None, str | None]:
    ipv4 = db.get_setting(IPV4_KEY) or (config.public_ip if config else None)
    return ipv4, db.get_setting(IPV6_KEY)
