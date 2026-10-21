"""Apps and the Supabase of this server: what a repository needs, connecting it, migrations, imports.

Mas reads the repository to adapt to the app instead of asking:

- a Supabase client library (supabase-js, @supabase/ssr, supabase-py…) says the app wants Supabase;
- the variable names of its example env files (``.env.example``…) say where the code reads the URL and
  the keys, so Mas fills those names (``VITE_SUPABASE_URL``, ``PUBLIC_SUPABASE_ANON_KEY``…) next to the
  usual ones. A secret (service_role key, Postgres URL) never goes to a name the browser can read;
- ``supabase/migrations/*.sql`` and ``supabase/seed.sql``, the layout of the Supabase CLI, describe the
  database the app needs. On every deploy of a connected app, the new migrations run before the build,
  in file name order, each in one transaction, and are recorded in
  ``supabase_migrations.schema_migrations`` as the Supabase CLI does. A failing migration stops the
  deploy: the site stays on its current version.

A dev database (a supabase.com project) can be copied in: its ``public`` schema, with or without the
rows, through ``pg_dump`` piped into the local Postgres. Its connection string is used once, passed in
the environment (never on a command line) and never stored. There is one Supabase per server: every
connected app shares its database.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from mas.config import Config
from mas.db import Database
from mas.deploy import DeployError, ReleaseLog
from mas.envcrypt import decrypt_env, encrypt_env, load_fernet
from mas.github import GitHubApp, GitHubError
from mas.host import Host
from mas.supabase import DB_CONTAINER, SupabaseError, SupabaseManager

log = logging.getLogger(__name__)

ENV_EXAMPLES = (".env.example", ".env.local.example", ".env.sample", ".env.template", ".env.dist",
                ".env.development.example", ".env.local.sample", "example.env")
DEP_FILES = ("package.json", "requirements.txt", "pyproject.toml", "Pipfile", "composer.json")
LIBRARY_RE = re.compile(r'"@supabase/(supabase-js|ssr|auth-helpers-[a-z]+|auth-ui-react)"|"@nuxtjs/supabase"'
                        r'|^\s*supabase\b|["\']supabase[>=<~"\' ]|supabase-community/', re.M)
ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
# Which value a variable name asks for; the first match wins (service before anon).
ROLES = (
    ("service_role_key", re.compile(r"SERVICE_ROLE|SUPABASE_SERVICE_KEY$|SUPABASE_SECRET(_KEY)?$")),
    ("anon_key", re.compile(r"SUPABASE_(ANON|PUBLISHABLE)(_KEY)?$|SUPABASE_(PUBLIC_)?KEY$")),
    ("url", re.compile(r"SUPABASE_(API_)?URL$")),
    ("database_url", re.compile(r"^(DATABASE_URL|DIRECT_URL|POSTGRES_(PRISMA_)?URL|POSTGRES_URL_NON_POOLING"
                                r"|SUPABASE_DB_URL|DB_URL)$")),
)
STANDARD = {"SUPABASE_URL": "url", "NEXT_PUBLIC_SUPABASE_URL": "url", "SUPABASE_ANON_KEY": "anon_key",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": "anon_key", "SUPABASE_SERVICE_ROLE_KEY": "service_role_key",
            "DATABASE_URL": "database_url"}
# Frameworks that only give the browser variables with a prefix.
PREFIXES = {"vite": "VITE_", "astro": "PUBLIC_", "@sveltejs/kit": "PUBLIC_", "expo": "EXPO_PUBLIC_"}
PUBLIC_PREFIXES = ("NEXT_PUBLIC_", "VITE_", "PUBLIC_", "EXPO_PUBLIC_", "NUXT_PUBLIC_", "REACT_APP_", "GATSBY_")
SECRET_ROLES = {"service_role_key", "database_url"}
MIGRATION_RE = re.compile(r"^(\d+)_([A-Za-z0-9_.-]+)\.sql$")
SOURCE_URL_RE = re.compile(r"^postgres(ql)?://[^\s'\"`$\\]+$")
TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
QUEUE_KEY = "supabase_connect_queue"
MIGRATIONS_TABLE = ("create schema if not exists supabase_migrations; create table if not exists "
                    "supabase_migrations.schema_migrations (version text primary key, statements text[], name text);")


class AppDbError(Exception):
    pass


def role_of(name: str) -> str | None:
    return next((role for role, pattern in ROLES if pattern.search(name)), None)


def env_names(text: str) -> list[str]:
    return list(dict.fromkeys(ENV_LINE_RE.findall(text)))


def migrations_in(paths: list[str], folder: str) -> list[dict]:
    """Supabase CLI migrations (``<version>_<name>.sql``) directly in ``folder``, in order."""
    out = []
    for path in paths:
        p = PurePosixPath(path)
        m = MIGRATION_RE.fullmatch(p.name)
        if str(p.parent) == folder and m:
            out.append({"version": m.group(1), "name": m.group(2), "file": p.name})
    return sorted(out, key=lambda m: (m["version"], m["file"]))


def detect(files: dict[str, str], paths: list[str], root: str = "") -> dict:
    """What a repository needs from Supabase. ``files``: text of the files Mas read, ``paths``: every file."""
    base = f"{root.strip('/')}/" if root.strip("/") else ""

    def first(names: tuple[str, ...]) -> list[tuple[str, str]]:
        found = []
        for name in names:
            for candidate in dict.fromkeys((base + name, name)):
                if candidate in files:
                    found.append((candidate, files[candidate]))
                    break
        return found

    evidence: list[str] = []
    deps = ""
    for path, text in first(DEP_FILES):
        deps += text
        if LIBRARY_RE.search(text):
            evidence.append(f"Supabase library in {path}")
    variables: dict[str, str] = {}
    for path, text in first(ENV_EXAMPLES):
        names = [n for n in env_names(text) if role_of(n)]
        variables.update({n: role_of(n) for n in names})
        if any(role_of(n) in ("url", "anon_key", "service_role_key") for n in names):
            evidence.append(f"Supabase variables in {path}")
    folder = next((f for f in dict.fromkeys((base + "supabase", "supabase"))
                   if any(p.startswith(f + "/") for p in paths)), None)
    migrations = migrations_in(paths, f"{folder}/migrations") if folder else []
    if folder and (migrations or f"{folder}/config.toml" in paths):
        evidence.append(f"{folder}/ ({len(migrations)} migration(s))")
    prefixes = sorted({prefix for dep, prefix in PREFIXES.items() if f'"{dep}"' in deps})
    return {"uses_supabase": bool(evidence), "evidence": evidence, "variables": variables, "prefixes": prefixes,
            "folder": folder, "migrations": migrations,
            "seed": bool(folder and f"{folder}/seed.sql" in paths)}


def connection_variables(found: dict, creds: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """(variables to set, names refused): the usual names, the framework ones and the ones the code reads."""
    wanted = dict(STANDARD)
    for prefix in found.get("prefixes", []):
        wanted.update({f"{prefix}SUPABASE_URL": "url", f"{prefix}SUPABASE_ANON_KEY": "anon_key"})
    wanted.update(found.get("variables", {}))
    out, refused = {}, []
    for name, role in wanted.items():
        if role in SECRET_ROLES and name.startswith(PUBLIC_PREFIXES):
            refused.append(name)  # the browser would get a key that bypasses every rule
        elif role in creds:
            out[name] = creds[role]
    return out, refused


def source_host(url: str) -> str:
    return urlsplit(url).hostname or "?"


class AppDatabases:
    def __init__(self, host: Host, config: Config, db: Database, supabase: SupabaseManager, deployer,
                 github: Callable[[], GitHubApp | None]) -> None:
        self.host, self.config, self.db = host, config, db
        self.supabase, self.deployer, self.github = supabase, deployer, github
        self._lock = threading.Lock()
        deployer.before_build.append(self.migrate_release)
        supabase.on_installed.append(self._connect_queued)

    # -- state ---------------------------------------------------------------------

    def _fernet(self):
        return load_fernet(self.host, self.config.secret_key_path)

    def _app(self, app_id: str) -> dict:
        app = self.db.get_app(app_id)
        if app is None:
            raise AppDbError(f"unknown app {app_id}")
        return app

    def _running(self) -> dict | None:
        inst = self.supabase.get()
        return inst if inst and inst["status"] == "running" and inst["secrets"] else None

    def credentials(self) -> dict[str, str]:
        if self._running() is None:
            raise SupabaseError("Supabase is not running on this server")
        c = self.supabase.credentials()
        return {"url": c["api_url"], "anon_key": c["anon_key"], "service_role_key": c["service_role_key"],
                "database_url": c["database_url"]}

    def connection(self, app: dict) -> str | None:
        """'local' (the Supabase of this server), 'external' (another project) or None."""
        env = decrypt_env(self._fernet(), app["env"])
        urls = {v.rstrip("/") for k, v in env.items() if role_of(k) == "url" and v}
        if not urls:
            return None
        inst = self.supabase.get()
        if inst and inst["secrets"] and self.supabase.ports(inst) and self.supabase.api_url(inst).rstrip("/") in urls:
            return "local"
        return "external"

    def scan(self, app: dict) -> dict:
        """Read the repository at the head of the deployed branch."""
        root = (app["root_dir"] or "").strip("/")
        gh = self.github()
        if gh is None or not app["repo"] or not app["installation_id"]:
            return detect({}, [], root)
        names = {n for name in DEP_FILES + ENV_EXAMPLES for n in (name, f"{root}/{name}" if root else name)}
        try:
            head = gh.head(app["installation_id"], app["repo"], app["branch"])
            paths = [f["path"] for f in gh.tree(app["installation_id"], app["repo"], head)]
            files = {}
            for path in [p for p in paths if p in names][:12]:
                raw, _ = gh.read_file(app["installation_id"], app["repo"], path, head)
                if raw is not None:
                    files[path] = raw[:200_000].decode("utf-8", "replace")
        except GitHubError as exc:
            log.info("repository scan of %s failed: %s", app["id"], exc)
            return {**detect({}, [], root), "error": str(exc)}
        return detect(files, paths, root)

    def applied(self) -> set[str]:
        out = self.supabase.run_sql(f"{MIGRATIONS_TABLE} select version from supabase_migrations.schema_migrations;",
                                    rows=True)
        return {line.strip() for line in out.splitlines() if line.strip()}

    def info(self, app_id: str) -> dict:
        app = self._app(app_id)
        inst = self.supabase.get()
        found = self.scan(app)
        connected = self.connection(app)
        applied: set[str] | None = None
        if connected == "local" and found["migrations"] and self._running():
            try:
                applied = self.applied()
            except SupabaseError:
                applied = None
        others = [a["id"] for a in self.db.list_apps() if a["id"] != app_id and a["mode"] == "managed"
                  and self.connection(a) == "local"]
        env = decrypt_env(self._fernet(), app["env"])
        return {
            "instance": inst["status"] if inst else None,
            "connected": connected,
            "variables": sorted(k for k in env if role_of(k) or k in STANDARD),
            "found": {k: found[k] for k in ("uses_supabase", "evidence", "variables", "folder", "seed")},
            "scan_error": found.get("error"),
            "migrations": [{**m, "applied": None if applied is None else m["version"] in applied}
                           for m in found["migrations"]],
            "seed_pending": self.db.get_setting(f"supabase_seed:{app_id}") == "1",
            "queued": app_id in self._queue(),
            "shared_with": others,
            "managed": app["mode"] == "managed",
        }

    # -- connecting ----------------------------------------------------------------

    def connect(self, app_id: str, actor: str, seed: bool = False, deploy: bool = True) -> dict:
        app = self._app(app_id)
        if app["mode"] != "managed":
            raise AppDbError("adopted services manage their own environment: copy the credentials instead")
        found = self.scan(app)
        variables, refused = connection_variables(found, self.credentials())
        fernet = self._fernet()
        env = decrypt_env(fernet, app["env"]) | variables
        self.db.update_app(app_id, env=encrypt_env(fernet, env))
        if seed and found["seed"]:
            self.db.set_setting(f"supabase_seed:{app_id}", "1")
        self.db.audit(actor, "supabase-connect", True, service_id=app_id, detail=", ".join(sorted(variables)))
        release_id = self.deployer.enqueue(app_id, "manual", actor) if deploy else None
        return {"variables": sorted(variables), "refused": refused, "release_id": release_id,
                "migrations": len(found["migrations"])}

    def _queue(self) -> list[str]:
        try:
            return json.loads(self.db.get_setting(QUEUE_KEY) or "[]")
        except ValueError:
            return []

    def queue(self, app_ids: list[str], seed: bool = False) -> None:
        """Connect these apps once the Supabase being installed is running."""
        for app_id in app_ids:
            if self._app(app_id)["mode"] != "managed":
                raise AppDbError(f"{app_id} is adopted: it manages its own environment")
            if seed:
                self.db.set_setting(f"supabase_seed:{app_id}", "1")
        self.db.set_setting(QUEUE_KEY, json.dumps(list(dict.fromkeys(self._queue() + app_ids))))

    def _connect_queued(self, rlog: ReleaseLog) -> None:
        queued, failed = self._queue(), []
        self.db.set_setting(QUEUE_KEY, None)
        for app_id in queued:
            try:
                result = self.connect(app_id, "mas", seed=self.db.get_setting(f"supabase_seed:{app_id}") == "1")
                rlog.line(f"connected {app_id}: {', '.join(result['variables'])}")
            except (AppDbError, SupabaseError) as exc:
                rlog.line(f"warning: could not connect {app_id}: {exc}")
                failed.append(app_id)
        if failed:
            self.db.set_setting(QUEUE_KEY, json.dumps(failed))

    # -- migrations (deploy hook) --------------------------------------------------------

    def migrate_release(self, app: dict, rel: str, rlog: ReleaseLog) -> None:
        """Apply the pending migrations of the release before it is built (apps connected here only)."""
        if app["mode"] != "managed" or self.connection(app) != "local":
            return
        root = (app["root_dir"] or "").strip("/")
        folder = next((f for f in (f"{rel}/{root}/supabase" if root else None, f"{rel}/supabase")
                       if f and self.host.is_dir(f)), None)
        if folder is None:
            return
        names = self.host.listdir(f"{folder}/migrations") if self.host.is_dir(f"{folder}/migrations") else []
        found = migrations_in([f"m/{n}" for n in names], "m")
        seed = self.db.get_setting(f"supabase_seed:{app['id']}") == "1" and self.host.is_file(f"{folder}/seed.sql")
        if not found and not seed:
            return
        rlog.step("Database migrations")
        with self._lock:
            try:
                done = self.applied()
                pending = [m for m in found if m["version"] not in done]
                for m in pending:
                    sql = self.host.read_text(f"{folder}/migrations/{m['file']}", max_bytes=20_000_000) or ""
                    rlog.line(f"applying {m['file']}")
                    self.supabase.run_sql_file(
                        f"{sql}\n;\ninsert into supabase_migrations.schema_migrations (version, name) "
                        f"values ('{m['version']}', '{m['name']}');\n")
                if seed:
                    rlog.line("loading supabase/seed.sql")
                    self.supabase.run_sql_file(self.host.read_text(f"{folder}/seed.sql", max_bytes=50_000_000) or "")
                    self.db.set_setting(f"supabase_seed:{app['id']}", None)
                if pending or seed:
                    self.supabase.run_sql("notify pgrst, 'reload schema';")
                rlog.line(f"{len(pending)} migration(s) applied, {len(found) - len(pending)} already there")
            except SupabaseError as exc:
                raise DeployError(f"migration failed, the site stays on its current version: {exc}") from None

    # -- importing a dev database --------------------------------------------------------

    @staticmethod
    def _env(url: str) -> dict[str, str]:
        return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/root"),
                "SRC": url}

    def _psql_source(self, url: str, query: str) -> str:
        lines: list[str] = []
        rc = self.host.stream(["docker", "exec", "-e", "SRC", DB_CONTAINER, "sh", "-c", f'psql "$SRC" -Atc "{query}"'],
                              lines.append, env=self._env(url), timeout=60)
        if rc != 0:
            text = " ".join(lines)[-400:].replace(url, "<connection string>")
            raise AppDbError(f"cannot read the dev database: {text or 'no answer'}")
        return "\n".join(lines)

    def import_dev(self, app_id: str, url: str, with_data: bool, replace: bool, actor: str) -> dict:
        """Copy the public schema of a dev database here, then connect the app if it is not yet."""
        app = self._app(app_id)
        url = url.strip()
        if not SOURCE_URL_RE.fullmatch(url):
            raise AppDbError("paste the Postgres connection string of the dev database (postgresql://…)")
        if self._running() is None:
            raise SupabaseError("Supabase is not running on this server")
        tables_sql = "select tablename from pg_tables where schemaname = 'public' order by 1"
        with self._lock:
            source_tables = [t for t in self._psql_source(url, tables_sql).splitlines() if TABLE_RE.fullmatch(t)]
            if not source_tables:
                raise AppDbError("the dev database has no table in its public schema")
            here = set(self.supabase.run_sql(f"{tables_sql};", rows=True).split())
            clash = [t for t in source_tables if t in here]
            if clash and not replace:
                raise AppDbError(f"these tables already exist here: {', '.join(clash)}. Tick “replace them” to "
                                 "drop them first")
            if clash:
                self.supabase.run_sql(" ".join(f'drop table if exists public."{t}" cascade;' for t in clash))
            major = int(self._psql_source(url, "show server_version_num").strip() or "0") // 10000
            dump = ('pg_dump "$SRC" --schema=public --no-owner --no-privileges --no-publications '
                    '--no-subscriptions' + ("" if with_data else " --schema-only"))
            keep = "grep -v -E '^(CREATE SCHEMA public|COMMENT ON SCHEMA public|ALTER SCHEMA public)'"
            load = (f"docker exec -i {DB_CONTAINER} psql -U postgres -d postgres -v ON_ERROR_STOP=1 "
                    "--single-transaction -q")
            if major <= 15:  # the pg_dump of the Supabase image is recent enough
                script = f"docker exec -e SRC {DB_CONTAINER} sh -c '{dump}' | {keep} | {load}"
            else:  # pg_dump must be at least as new as the server it reads
                script = f"docker run --rm -e SRC postgres:{major}-alpine sh -c '{dump}' | {keep} | {load}"
            lines: list[str] = []
            rc = self.host.stream(["bash", "-o", "pipefail", "-c", script], lines.append, env=self._env(url),
                                  timeout=3600)
            if rc != 0:
                text = "\n".join(lines)[-800:].replace(url, "<connection string>")
                raise AppDbError(f"import failed: {text or 'no output'}")
            self.supabase.run_sql("notify pgrst, 'reload schema';")
        self.db.audit(actor, "supabase-import", True, service_id=app_id,
                      detail=f"{source_host(url)}: {len(source_tables)} table(s){' with data' if with_data else ''}")
        result: dict = {"tables": source_tables, "replaced": clash, "connected": None}
        if self.connection(app) != "local" and app["mode"] == "managed":
            result["connected"] = self.connect(app_id, actor)
        return result
