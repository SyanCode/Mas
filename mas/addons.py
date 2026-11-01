"""Search mods and plugins for a Minecraft server: Modrinth (mods, plugins, modpacks) and SpigotMC (via Spiget).

Read-only lookups of public catalogs; what the user picks ends up in the service's options
(``modrinth`` / ``spiget``) and the image downloads it at the next start.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

MODRINTH = "https://api.modrinth.com/v2"
SPIGET = "https://api.spiget.org/v2"
USER_AGENT = "Mas/1 (self-hosted dashboard; github.com/SyanCode/Mas)"
VERSION_RE = re.compile(r"^\d+(\.\d+){1,2}$")

# Modrinth tags a plugin with the platforms it runs on: one list per server type.
LOADERS = {
    "PAPER": ["paper", "spigot", "bukkit"], "PURPUR": ["purpur", "paper", "spigot", "bukkit"],
    "FOLIA": ["folia"], "SPIGOT": ["spigot", "bukkit"], "FABRIC": ["fabric"], "QUILT": ["quilt", "fabric"],
    "FORGE": ["forge"], "NEOFORGE": ["neoforge"],
}


class AddonError(Exception):
    pass


def _get(url: str, timeout: float = 10) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read(2_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise AddonError(f"the catalog did not answer ({getattr(exc, 'reason', exc)})") from None


def supports_search(server_type: str) -> bool:
    return server_type in LOADERS


def search_modrinth(query: str, server_type: str, version: str = "", limit: int = 20) -> list[dict]:
    """Projects matching the query that run on this kind of server (and Minecraft version, when one is set)."""
    loaders = LOADERS.get(server_type)
    if not loaders:
        raise AddonError("this server type takes no mods or plugins one by one")
    # Plugins are a project type of their own on Modrinth: asking for "mod" alone hides EssentialsX & co.
    facets: list[list[str]] = [[f"categories:{name}" for name in loaders], ["project_type:mod", "project_type:plugin"]]
    if VERSION_RE.fullmatch(version):
        facets.append([f"versions:{version}"])
    params = urlencode({"query": query[:80], "limit": max(1, min(limit, 30)), "facets": json.dumps(facets)})
    data = _get(f"{MODRINTH}/search?{params}")
    return [{"id": h["slug"], "name": h["title"], "description": h.get("description", ""),
             "icon": h.get("icon_url") or "", "downloads": h.get("downloads", 0), "author": h.get("author", ""),
             "url": f"https://modrinth.com/project/{h['slug']}", "source": "modrinth"}
            for h in data.get("hits", [])]


def search_spiget(query: str, limit: int = 20) -> list[dict]:
    if not query.strip():
        raise AddonError("type a name to search SpigotMC")
    params = urlencode({"size": max(1, min(limit, 30)), "sort": "-downloads",
                        "fields": "id,name,tag,downloads,premium,external"})
    data = _get(f"{SPIGET}/search/resources/{quote(query[:80], safe='')}?{params}")
    return [{"id": str(h["id"]), "name": h["name"], "description": h.get("tag", ""), "icon": "",
             "downloads": h.get("downloads", 0), "author": "",
             "url": f"https://www.spigotmc.org/resources/{h['id']}/", "source": "spiget"}
            for h in data if not h.get("premium") and not h.get("external")]  # only free, hosted on SpigotMC


def describe(modrinth: list[str], spiget: list[str]) -> list[dict]:
    """Names and icons of what is already selected (best effort: an unknown entry keeps its identifier)."""
    out = {m: {"id": m, "name": m, "description": "", "icon": "", "source": "modrinth", "url": ""}
           for m in modrinth}
    slugs = [m.split(":")[0] for m in modrinth if not m.startswith("http")]
    if slugs:
        try:
            for p in _get(f"{MODRINTH}/projects?ids={quote(json.dumps(slugs))}"):
                for entry in modrinth:
                    if entry.split(":")[0] in (p["slug"], p["id"]):
                        out[entry].update(name=p["title"], description=p.get("description", ""),
                                          icon=p.get("icon_url") or "", url=f"https://modrinth.com/project/{p['slug']}")
        except AddonError:
            pass
    for rid in spiget:
        entry = {"id": rid, "name": f"SpigotMC #{rid}", "description": "", "icon": "", "source": "spiget",
                 "url": f"https://www.spigotmc.org/resources/{rid}/"}
        try:
            data = _get(f"{SPIGET}/resources/{quote(rid, safe='')}?fields=name,tag", timeout=5)
            entry.update(name=data.get("name", entry["name"]), description=data.get("tag", ""))
        except AddonError:
            pass
        out[f"spiget:{rid}"] = entry
    return list(out.values())
