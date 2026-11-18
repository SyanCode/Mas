"""Known vulnerabilities in the dependencies of the apps: the versions really installed, checked every day.

Outside scanners read the version of a framework from the pages of a site (Next.js writes it in its
JavaScript) and send "your site is out of date" e-mails. Mas looks first: for each app it reads the lock
files of the release that is live (package-lock.json, composer.lock, poetry.lock, uv.lock,
requirements.txt), asks the public OSV database (https://osv.dev, run by Google, free, no account) which
of these exact versions have a published security advisory, and keeps the answer.

What leaves the server: the names and versions of the packages, nothing else (no code, no file, no
domain). Development-only packages are not checked (they are not shipped). Turn it off in Settings or
with ``mas deps --off``.

The result becomes health checks (a vulnerable direct dependency is an error, so the server alerts tell
the administrators once), is given to the advisor, and names the version to upgrade to.
"""

from __future__ import annotations

import json
import logging
import re
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

from mas.config import Config
from mas.db import Database
from mas.host import Host

log = logging.getLogger(__name__)

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
SETTING = "deps:settings"
RESULT = "deps:app:"  # + app id
LAST = "deps:last_run"
DAY = 86400
MIN_GAP = 600  # seconds between two scans of the same app, whatever the buttons say
SETTLE_SECONDS = 60  # a new release is checked a minute after it went live
LOCK_BYTES = 30 * 1024 * 1024
BATCH = 500
TIMEOUT = 20
MAX_VULN_DETAILS = 200
SEVERITIES = ("critical", "high", "moderate", "low", "unknown")
DEFAULTS = {"enabled": True}


class DepAuditError(Exception):
    pass


# -- versions ---------------------------------------------------------------------------------------


def version_key(version: str) -> tuple:
    """Order of versions good enough for npm, PyPI and Composer: numbers first, a pre-release before its release."""
    v = version.strip().lstrip("vV=")
    main, _, pre = v.partition("-")
    if not pre:
        m = re.match(r"^([0-9.]+)((?:a|b|rc|dev|alpha|beta)[0-9.]*)$", main)  # PyPI 1.0rc1
        if m:
            main, pre = m.group(1), m.group(2)
    main = main.split("+", 1)[0]
    nums = tuple(int(x) if x.isdigit() else 0 for x in main.split(".") if x != "")
    nums = nums + (0,) * (4 - len(nums)) if len(nums) < 4 else nums
    return nums, (0, pre) if pre else (1, "")


def _in_range(version: str, events: list[dict]) -> tuple[bool, str | None]:
    """Is ``version`` inside this OSV range, and the version that fixes it (None: no fix published)."""
    key = version_key(version)
    inside, fixed = False, None
    for ev in sorted(events, key=lambda e: version_key(next(iter(e.values())) if e else "0")):
        if "introduced" in ev:
            start = ev["introduced"]
            if start == "0" or version_key(start) <= key:
                inside, fixed = True, None
        elif "fixed" in ev:
            if inside and key < version_key(ev["fixed"]):
                return True, ev["fixed"]
            inside = False
        elif "last_affected" in ev:
            if inside and key <= version_key(ev["last_affected"]):
                return True, None
            inside = False
        elif "limit" in ev:
            if inside and key >= version_key(ev["limit"]):
                inside = False
    return inside, fixed


# -- lock files -------------------------------------------------------------------------------------


def _pkg(ecosystem: str, name: str, version: str, direct: bool) -> dict:
    return {"ecosystem": ecosystem, "name": name, "version": version, "direct": direct}


def parse_package_json(text: str | None) -> set[str]:
    """Names of the runtime dependencies (not devDependencies)."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    out: set[str] = set()
    for key in ("dependencies", "optionalDependencies", "peerDependencies"):
        if isinstance(data.get(key), dict):
            out |= set(data[key])
    return out


def parse_package_lock(text: str, direct: set[str]) -> list[dict]:
    """package-lock.json v1, v2 and v3; development-only packages are left out."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out, seen = [], set()
    if isinstance(data.get("packages"), dict):
        for path, meta in data["packages"].items():
            if not path or not isinstance(meta, dict) or meta.get("dev") or meta.get("link"):
                continue
            name = meta.get("name") or path.rsplit("node_modules/", 1)[-1]
            version = str(meta.get("version") or "")
            if not name or not re.match(r"^\d", version) or (name, version) in seen:
                continue
            seen.add((name, version))
            top = path == f"node_modules/{name}"
            out.append(_pkg("npm", name, version, top and name in direct))
        return out

    def walk(deps: dict, top: bool) -> None:
        for name, meta in deps.items():
            if not isinstance(meta, dict) or meta.get("dev"):
                continue
            version = str(meta.get("version") or "")
            if re.match(r"^\d", version) and (name, version) not in seen:
                seen.add((name, version))
                out.append(_pkg("npm", name, version, top and name in direct))
            if isinstance(meta.get("dependencies"), dict):
                walk(meta["dependencies"], False)

    if isinstance(data.get("dependencies"), dict):
        walk(data["dependencies"], True)
    return out


def parse_composer_lock(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for meta in data.get("packages") or []:  # "packages-dev" is left out
        if isinstance(meta, dict) and meta.get("name") and meta.get("version"):
            out.append(_pkg("Packagist", meta["name"], str(meta["version"]).lstrip("v"), True))
    return out


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(text: str) -> list[dict]:
    """Only exact pins (name==1.2.3): a range does not say which version is installed."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([0-9][^\s;,]*)", line)
        if m:
            out.append(_pkg("PyPI", _norm_py(m.group(1)), m.group(2), True))
    return out


def pyproject_direct(text: str | None) -> set[str]:
    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return set()
    names = set()
    for dep in (data.get("project") or {}).get("dependencies") or []:
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(dep))
        if m:
            names.add(_norm_py(m.group(1)))
    names |= {_norm_py(n) for n in ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}}
    return names - {"python"}


def parse_toml_lock(text: str, direct: set[str]) -> list[dict]:
    """poetry.lock and uv.lock: [[package]] name / version (poetry's dev group is left out)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    out = []
    for meta in data.get("package") or []:
        if not isinstance(meta, dict) or not meta.get("name") or not meta.get("version"):
            continue
        if meta.get("category") == "dev" or (isinstance(meta.get("source"), dict) and
                                             ("editable" in meta["source"] or "virtual" in meta["source"])):
            continue
        name = _norm_py(meta["name"])
        out.append(_pkg("PyPI", name, str(meta["version"]), name in direct))
    return out


def app_folder(config: Config, app: dict) -> str | None:
    if app.get("mode") == "adopted":
        return app.get("path") or None
    from mas.deploy import app_paths
    return app_paths(config, app["id"]).current


def read_packages(host: Host, folder: str) -> tuple[list[dict], list[str]]:
    """The packages installed in a folder (and the lock files read)."""
    def read(name: str, size: int = LOCK_BYTES) -> str | None:
        path = f"{folder.rstrip('/')}/{name}"
        return host.read_text(path, size) if host.exists(path) else None

    pkgs: list[dict] = []
    used: list[str] = []
    lock = read("package-lock.json") or read("npm-shrinkwrap.json")
    if lock:
        pkgs += parse_package_lock(lock, parse_package_json(read("package.json", 1024 * 1024)))
        used.append("package-lock.json")
    composer = read("composer.lock")
    if composer:
        pkgs += parse_composer_lock(composer)
        used.append("composer.lock")
    direct = pyproject_direct(read("pyproject.toml", 1024 * 1024))
    for name in ("poetry.lock", "uv.lock"):
        text = read(name)
        if text:
            pkgs += parse_toml_lock(text, direct)
            used.append(name)
            break
    else:
        req = read("requirements.txt", 1024 * 1024)
        if req:
            pkgs += parse_requirements(req)
            used.append("requirements.txt")
    seen, out = set(), []
    for p in pkgs:
        key = (p["ecosystem"], p["name"], p["version"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out, used


# -- OSV --------------------------------------------------------------------------------------------


def _http_json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": "Mas dependency audit"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - fixed https URL
            return json.loads(resp.read(20 * 1024 * 1024))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise DepAuditError(f"OSV unreachable: {exc}") from None


def severity_of(vuln: dict) -> str:
    raw = str((vuln.get("database_specific") or {}).get("severity") or "").lower()
    if raw in ("critical", "high", "moderate", "low"):
        return raw
    if raw == "medium":
        return "moderate"
    for aff in vuln.get("affected") or []:
        raw = str((aff.get("ecosystem_specific") or {}).get("severity") or "").lower()
        if raw in SEVERITIES:
            return raw
    return "unknown"


def describe_vuln(vuln: dict, pkg: dict) -> dict:
    """What matters of an advisory for this package and version."""
    fixed: str | None = None
    affected = False
    for aff in vuln.get("affected") or []:
        p = aff.get("package") or {}
        if p.get("ecosystem") != pkg["ecosystem"]:
            continue
        name = _norm_py(p.get("name", "")) if pkg["ecosystem"] == "PyPI" else p.get("name")
        if name != pkg["name"]:
            continue
        if pkg["version"] in (aff.get("versions") or []):
            affected = True
        for r in aff.get("ranges") or []:
            if r.get("type") not in ("SEMVER", "ECOSYSTEM"):
                continue
            inside, fix = _in_range(pkg["version"], r.get("events") or [])
            if inside:
                affected = True
                if fix and (fixed is None or version_key(fix) < version_key(fixed)):
                    fixed = fix
    aliases = [a for a in vuln.get("aliases") or [] if a.startswith("CVE-")]
    return {"id": vuln.get("id", "?"), "cve": aliases[:3], "summary": str(vuln.get("summary") or "")[:200],
            "severity": severity_of(vuln), "fixed": fixed, "affected": affected or not vuln.get("affected"),
            "url": f"https://osv.dev/vulnerability/{vuln.get('id', '')}"}


class OSV:
    """The OSV database, with the advisories already read kept for a day."""

    def __init__(self, fetch: Callable[[str, dict | None], dict] = _http_json) -> None:
        self.fetch = fetch
        self.cache: dict[str, tuple[float, dict]] = {}

    def query(self, pkgs: list[dict]) -> list[list[str]]:
        """For each package, the ids of the advisories of its exact version."""
        out: list[list[str]] = []
        for i in range(0, len(pkgs), BATCH):
            chunk = pkgs[i:i + BATCH]
            queries = [{"package": {"name": p["name"], "ecosystem": p["ecosystem"]}, "version": p["version"]} for p in chunk]
            res = self.fetch(OSV_BATCH, {"queries": queries}).get("results") or []
            if len(res) != len(chunk):
                raise DepAuditError("OSV answered an unexpected number of results")
            out += [[v["id"] for v in (r or {}).get("vulns") or [] if v.get("id")] for r in res]
        return out

    def vuln(self, vid: str, now: float) -> dict:
        hit = self.cache.get(vid)
        if hit and now - hit[0] < DAY:
            return hit[1]
        if not re.fullmatch(r"[A-Za-z0-9._:-]{3,80}", vid):
            raise DepAuditError(f"odd advisory id {vid!r}")
        data = self.fetch(OSV_VULN + vid, None)
        self.cache[vid] = (now, data)
        return data


# -- the service ------------------------------------------------------------------------------------


def summarize(pkgs: list[dict], ids: list[list[str]], osv: OSV, now: float) -> list[dict]:
    """The vulnerable packages, most serious first, each with the version that fixes all its advisories."""
    findings, fetched = [], 0
    for pkg, vids in zip(pkgs, ids):
        if not vids:
            continue
        vulns = []
        for vid in vids:
            if fetched >= MAX_VULN_DETAILS:
                vulns.append({"id": vid, "cve": [], "summary": "", "severity": "unknown", "fixed": None,
                              "affected": True, "url": f"https://osv.dev/vulnerability/{vid}"})
                continue
            fetched += 1
            v = describe_vuln(osv.vuln(vid, now), pkg)
            if v["affected"]:
                vulns.append(v)
        if not vulns:
            continue
        fixes = [v["fixed"] for v in vulns if v["fixed"]]
        upgrade = max(fixes, key=version_key) if fixes else None
        worst = min((SEVERITIES.index(v["severity"]) for v in vulns), default=len(SEVERITIES) - 1)
        findings.append({**pkg, "severity": SEVERITIES[worst], "upgrade_to": upgrade,
                         "fixable": all(v["fixed"] for v in vulns), "vulns": vulns})
    findings.sort(key=lambda f: (not f["direct"], SEVERITIES.index(f["severity"]), f["name"]))
    return findings


class DepAudit:
    def __init__(self, db: Database, host: Host, config: Config, osv: OSV | None = None) -> None:
        self.db, self.host, self.config = db, host, config
        self.osv = osv or OSV()

    def settings(self) -> dict:
        raw = json.loads(self.db.get_setting(SETTING) or "null") or {}
        return {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}, "last_run": self.db.get_setting(LAST)}

    def configure(self, enabled: bool) -> dict:
        self.db.set_setting(SETTING, json.dumps({"enabled": bool(enabled)}))
        return self.settings()

    def latest(self, app_id: str) -> dict | None:
        return json.loads(self.db.get_setting(RESULT + app_id) or "null")

    def apps(self) -> list[dict]:
        return [a for a in self.db.list_apps() if a.get("environment") != "preview"]

    def report(self) -> list[dict]:
        out = []
        for app in self.apps():
            res = self.latest(app["id"])
            out.append({"app": app["id"], "name": app.get("name") or app["id"], "domain": app.get("domain"),
                        **(res or {"at": None, "findings": [], "packages": 0, "lockfiles": [], "error": None})})
        return out

    def run(self, apps: list[dict] | None = None, trigger: str = "manual", now: float | None = None) -> list[dict]:
        """Check the apps now (all of them by default) with one OSV query for all their packages."""
        now = now or time.time()
        apps = self.apps() if apps is None else apps
        per_app: dict[str, tuple[dict, list[dict], list[str], int | None]] = {}
        for app in apps:
            folder = app_folder(self.config, app)
            if not folder or not self.host.exists(folder):
                continue
            pkgs, used = read_packages(self.host, folder)
            live = next((r for r in self.db.list_releases(app["id"], 5) if r["status"] == "live"), None)
            per_app[app["id"]] = (app, pkgs, used, live["id"] if live else None)
        unique: dict[tuple, dict] = {}
        for _, pkgs, _, _ in per_app.values():
            for p in pkgs:
                unique.setdefault((p["ecosystem"], p["name"], p["version"]), p)
        keys = list(unique)
        error = None
        try:
            ids = dict(zip(keys, self.osv.query([unique[k] for k in keys]))) if keys else {}
        except DepAuditError as exc:
            ids, error = {}, str(exc)
        at = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        results = []
        for app_id, (app, pkgs, used, release_id) in per_app.items():
            if error:  # keep what was known, say the check failed
                res = self.latest(app_id) or {"findings": [], "packages": len(pkgs), "lockfiles": used}
                res.update(error=error, checked_at=at)
            else:
                try:
                    findings = summarize(pkgs, [ids[(p["ecosystem"], p["name"], p["version"])] for p in pkgs], self.osv, now)
                    res = {"findings": findings, "packages": len(pkgs), "lockfiles": used, "error": None}
                except DepAuditError as exc:
                    res = (self.latest(app_id) or {"findings": [], "packages": len(pkgs), "lockfiles": used})
                    res.update(error=str(exc))
                res.update(at=at, checked_at=at)
            res.update(release_id=release_id, trigger=trigger)
            self.db.set_setting(RESULT + app_id, json.dumps(res))
            n = len(res["findings"])
            self.db.audit("deps", "deps-audit", not res.get("error"), service_id=app_id,
                          detail=(res.get("error") or f"{res['packages']} packages, {n} vulnerable")[:300])
            results.append({"app": app_id, **res})
        self.db.set_setting(LAST, at)
        return results

    def due(self, now: float) -> list[dict]:
        """Apps whose live release is not checked yet (a minute after it went live), or not checked for a day."""
        out = []
        for app in self.apps():
            res = self.latest(app["id"])
            live = next((r for r in self.db.list_releases(app["id"], 5) if r["status"] == "live"), None)
            checked = _ts(res.get("checked_at") or res.get("at")) if res else 0.0
            if now - checked < MIN_GAP:
                continue
            if res is None or now - checked >= DAY:
                out.append(app)
            elif live and res.get("release_id") != live["id"]:
                finished = _ts(live.get("finished_at") or live.get("created_at"))
                if now - finished >= SETTLE_SECONDS:
                    out.append(app)
        return out

    def tick(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        if not self.settings()["enabled"]:
            return []
        apps = self.due(now)
        if not apps:
            return []
        if len(apps) < len(self.apps()) and self._all_stale(now):
            apps = self.apps()  # the daily round: one query for every app
        return [r["app"] for r in self.run(apps, "scheduled", now)]

    def _all_stale(self, now: float) -> bool:
        last = _ts(self.db.get_setting(LAST))
        return now - last >= DAY


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


# -- health checks ----------------------------------------------------------------------------------


def upgrade_command(f: dict) -> str:
    target = f["upgrade_to"]
    if f["ecosystem"] == "npm":
        return f"npm install {f['name']}@{target}" if f["direct"] else f"npm update {f['name']}   # or npm audit fix"
    if f["ecosystem"] == "PyPI":
        return f"set {f['name']}=={target} in requirements / pyproject, then reinstall"
    return f"composer update {f['name']}"


def checks_from(db: Database) -> list:
    """Health checks from the last results: a vulnerable package shipped with an app.

    error: a direct dependency (what outside scanners see and what the app calls) or a high / critical advisory;
    warning: the other transitive ones."""
    from mas.health import Check

    if not (json.loads(db.get_setting(SETTING) or "null") or DEFAULTS).get("enabled", True):
        return []
    out = []
    for app in db.list_apps():
        if app.get("environment") == "preview":
            continue
        res = json.loads(db.get_setting(RESULT + app["id"]) or "null")
        if not res:
            continue
        name = app.get("name") or app["id"]
        findings = res.get("findings") or []
        for f in findings[:12]:
            ids = ", ".join(v["id"] + (f" ({v['cve'][0]})" if v.get("cve") else "") for v in f["vulns"][:4])
            more = f" and {len(f['vulns']) - 4} more" if len(f["vulns"]) > 4 else ""
            fix = (f"Upgrade {f['name']} to {f['upgrade_to']} or later ({upgrade_command(f)}), commit the lock file "
                   "and deploy. The robot can do it: Advisor → hand the proposal to the robot."
                   if f["upgrade_to"] else "No fixed version is published yet: read the advisory for a workaround.")
            level = "error" if f["direct"] or f["severity"] in ("critical", "high") else "warning"
            kind = "dependency" if f["direct"] else "sub-dependency"
            out.append(Check(level, "dependencies",
                             f"{name}: {f['name']} {f['version']} has {len(f['vulns'])} known "
                             f"vulnerabilit{'y' if len(f['vulns']) == 1 else 'ies'} ({f['severity']})",
                             f"{kind.capitalize()} of {name}: {ids}{more}. "
                             + (f["vulns"][0]["summary"] + ". " if f["vulns"][0].get("summary") else "")
                             + "Scanners read framework versions from the pages and the site becomes a target.",
                             fix, None, app["id"]))
        if len(findings) > 12:
            out.append(Check("warning", "dependencies", f"{name}: {len(findings) - 12} more vulnerable packages",
                             "See `mas deps " + app["id"] + "` for the full list.", "npm audit fix   # or the tool of the language"))
        if res.get("error"):
            out.append(Check("info", "dependencies", f"{name}: the dependency check failed", res["error"][:300]))
    return out
