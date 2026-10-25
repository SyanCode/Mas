"""Names of an app: the www of a root domain, and whether each name points to this server.

- An app given a root domain (``klarfi.com``) also answers to ``www.klarfi.com``: the alias is added with
  the domain, its DNS record is created with the domain's when the registrar is connected, and the
  certificate covers both.
- Like Vercel, every name is checked: it points here (valid configuration), elsewhere, or nowhere, with
  the records to add. A deployment logs the check; the certificate only asks for names that resolve, so
  a missing www never costs the domain its HTTPS (the next deployment adds it once it points here).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from mas.db import Database

# Public suffixes of two labels: "example.co.uk" is a root domain like "example.com".
SECOND_LEVEL = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au", "co.nz",
    "org.nz", "co.jp", "ne.jp", "or.jp", "com.br", "net.br", "com.mx", "com.ar", "com.tr", "co.za", "co.in",
    "co.kr", "com.cn", "com.hk", "com.sg", "com.tw", "co.il", "com.pl", "co.at", "or.at", "asso.fr", "gouv.fr",
}


def is_root(domain: str) -> bool:
    """``klarfi.com`` or ``shop.co.uk``, not ``app.klarfi.com`` nor ``www.klarfi.com``."""
    labels = domain.lower().strip(".").split(".")
    return labels[0] != "www" and (len(labels) == 2 or (len(labels) == 3 and ".".join(labels[1:]) in SECOND_LEVEL))


def www_of(domain: str | None) -> str | None:
    return f"www.{domain.lower()}" if domain and is_root(domain) else None


def record_name(name: str) -> str:
    """Name of the record at the registrar: "@" for a root domain, the sub-domain otherwise."""
    labels = name.lower().split(".")
    keep = 3 if ".".join(labels[-2:]) in SECOND_LEVEL else 2
    return ".".join(labels[:-keep]) or "@"


def point_www(provider, zone: str, subdomain: str, ipv4: str, ipv6: str | None, overwrite: bool = False) -> list[str]:
    """With the record of a root domain, the one of its www (a CNAME there is left as it is)."""
    from mas.dnsproviders import DnsConflict, DnsError, point_domain

    if subdomain or not is_root(zone):
        return []
    try:
        return [f"www: {c}" for c in point_domain(provider, zone, "www", ipv4, ipv6, overwrite)["changes"]]
    except DnsConflict as exc:
        if exc.kind == "cname":
            return [f"www: CNAME {exc.records[0]['target']} (kept)"]
        return [f"www: {exc} (not changed)"]
    except DnsError as exc:
        return [f"www: {exc}"]


def names_in_use(db: Database, except_app: str | None = None) -> dict[str, str]:
    """Every domain and alias of the apps: {name: app id}."""
    out: dict[str, str] = {}
    for a in db.list_apps():
        if a["id"] == except_app:
            continue
        for name in [a.get("domain"), *json.loads(a.get("aliases") or "[]")]:
            if name:
                out[name] = a["id"]
    return out


def aliases_for(db: Database, app_id: str, domain: str | None, current: list[str] | None = None,
                old_domain: str | None = None) -> list[str]:
    """The aliases of an app given ``domain``: the www of the old domain goes, the www of a root domain comes
    (unless another app already answers to it). Other aliases are kept."""
    aliases = [a for a in (current or []) if a != f"www.{old_domain}" and a != domain]
    www = www_of(domain)
    if www and www not in aliases and www not in names_in_use(db, except_app=app_id):
        aliases.append(www)
    return aliases


def check_name(name: str, root: str | None, expected: tuple[str | None, str | None],
               resolver: Callable[[str], list[str]]) -> dict:
    """Does ``name`` point to this server? ``root``: the domain ``name`` is the www of."""
    ipv4, ipv6 = expected
    ips = resolver(name)
    here = {ip for ip in (ipv4, ipv6) if ip}
    status = "missing" if not ips else "ok" if here & set(ips) else "elsewhere"
    records = []
    if root:
        records.append({"type": "CNAME", "name": "www", "value": f"{root}."})
        if ipv4:
            records.append({"type": "A", "name": "www", "value": ipv4})
    else:
        if ipv4:
            records.append({"type": "A", "name": record_name(name), "value": ipv4})
        if ipv6:
            records.append({"type": "AAAA", "name": record_name(name), "value": ipv6})
    return {"name": name, "status": status, "ips": ips, "records": records}


def check_app(app: dict, expected: tuple[str | None, str | None],
              resolver: Callable[[str], list[str]]) -> list[dict]:
    """The domain and every alias of ``app``, checked."""
    domain = app.get("domain")
    if not domain:
        return []
    aliases = json.loads(app.get("aliases") or "[]")
    return [check_name(domain, None, expected, resolver),
            *(check_name(a, domain if a == f"www.{domain}" else None, expected, resolver) for a in aliases)]


def describe(check: dict) -> str:
    """One line of a deployment log, the way Vercel shows a domain."""
    if check["status"] == "ok":
        return f"✓ {check['name']}: valid configuration ({', '.join(check['ips'])})"
    want = " or ".join(f"{r['type']} {r['name']} → {r['value']}" for r in check["records"]) or "a record to this server"
    if check["status"] == "missing":
        return f"✗ {check['name']}: no DNS record, add {want}"
    return f"! {check['name']}: points to {', '.join(check['ips'])}, not to this server; expected {want}"
