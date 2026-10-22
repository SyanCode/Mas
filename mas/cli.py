"""``mas`` command line. Same modules as the API, no HTTP involved."""

from __future__ import annotations

import getpass
import ipaddress
import logging
import os
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from mas import __version__, auth
from mas.actions import CONFIRM_ACTIONS, ActionError, available_actions, nginx_reload, run_action
from mas.config import Config, load_config
from mas.correlator import scan as run_scan
from mas.db import Database
from mas.host import FixtureHost, Host
from mas.logs import get_logs
from mas.models import ScanResult, Service, Status
from mas.state import run_scan as run_scan_with_meta

SSL_WARN_DAYS = 14

app = typer.Typer(help="Mas: discover and manage what already runs on this server.", add_completion=False)
user_app = typer.Typer(help="Manage the admin account and its two-factor authentication.",
                       no_args_is_help=True)
nginx_app = typer.Typer(help="nginx helpers.", no_args_is_help=True)
app.add_typer(user_app, name="user")
app.add_typer(nginx_app, name="nginx")

console = Console()
err_console = Console(stderr=True)

STATUS_STYLE = {
    Status.RUNNING: ("●", "green"),
    Status.STOPPED: ("●", "red"),
    Status.ERRORED: ("●", "bright_red"),
    Status.UNKNOWN: ("○", "dim"),
}


class Env:
    """Host, config and database for the current invocation."""

    def __init__(self, fixture: Path | None = None, db_path: Path | None = None) -> None:
        self.host = FixtureHost(fixture) if fixture else Host()
        self.config: Config = load_config(self.host)
        if db_path:
            self.config.db_path = str(db_path)
        self._db: Database | None = None

    @property
    def db(self) -> Database:
        if self._db is None:
            try:
                self._db = Database(self.config.db_path)
            except (OSError, PermissionError) as exc:
                if not is_root():
                    fail(f"Mas needs administrator rights for this. Run: [bold]{sudo_hint(sys.argv[1:])}[/]")
                fail(f"cannot open {self.config.db_path} ({exc})")
        return self._db


def fail(message: str, code: int = 1) -> None:
    err_console.print(f"[red]error:[/] {message}")
    raise typer.Exit(code)


def actor() -> str:
    return f"cli:{os.environ.get('SUDO_USER') or getpass.getuser()}"


NO_UPDATE_CHECK = {"self-update", "serve"}


def notify_update(config: Config) -> None:
    """One line on stderr when the installed Mas is behind the official repository."""
    from mas.selfupdate import update_available

    if os.environ.get("MAS_NO_UPDATE_CHECK") or not err_console.is_terminal:
        return
    behind = update_available(config)
    if behind:
        err_console.print(f"[yellow]A new version of Mas is available[/] ({behind} commit{'s' if behind > 1 else ''} "
                          "behind). Update with: [bold]sudo mas self-update[/]\n")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
    fixture: Annotated[Optional[Path], typer.Option(hidden=True, help="Fixture server (development).")] = None,
    db: Annotated[Optional[Path], typer.Option(hidden=True, help="Database path (development).")] = None,
) -> None:
    if version:
        from mas.banner import colors, render
        if colors():  # a terminal: the logo; a script still gets "mas <version>"
            sys.stdout.write(render(__version__))
        else:
            print(f"mas {__version__}")
        raise typer.Exit()
    ctx.obj = Env(fixture, db)
    if fixture is None and ctx.invoked_subcommand not in NO_UPDATE_CHECK:
        notify_update(ctx.obj.config)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


# -- scan ----------------------------------------------------------------------


def _ssl_cell(svc: Service, now: datetime) -> str:
    if svc.ssl_expires is None:
        return "[dim]-[/]"
    days = (svc.ssl_expires - now).days
    date = svc.ssl_expires.strftime("%Y-%m-%d")
    if days < 0:
        return f"[bold red]{date} (expired)[/]"
    if days < SSL_WARN_DAYS:
        return f"[dark_orange]{date} ({days}d)[/]"
    return f"{date} [dim]({days}d)[/]"


def _git_cell(svc: Service) -> str:
    if not svc.git_remote:
        return "[dim]-[/]"
    remote = svc.git_remote
    for prefix in ("https://github.com/", "git@github.com:"):
        if remote.startswith(prefix):
            remote = "gh:" + remote[len(prefix):].removesuffix(".git")
    branch = f"@{svc.git_branch}" if svc.git_branch else ""
    return f"{remote}{branch}"


def render_table(result: ScanResult) -> Table:
    now = datetime.now(timezone.utc)
    table = Table(title=f"{len(result.services)} services", title_justify="left", header_style="bold")
    for col in ("", "Service", "Domains", "Type", "Manager", "Port", "PID", "User", "Path", "SSL", "Git"):
        table.add_column(col, overflow="fold")
    for svc in result.services:
        dot, color = STATUS_STYLE[svc.status]
        domains = "\n".join(svc.domains) if svc.domains else "[dim italic]no domain[/]"
        type_cell = svc.type.value + ("*" if svc.type_source == "manual" else "")
        table.add_row(
            f"[{color}]{dot}[/]",
            svc.display_name or svc.name,
            domains,
            type_cell,
            svc.manager.value,
            str(svc.port) if svc.port else "[dim]-[/]",
            str(svc.pid) if svc.pid else "[dim]-[/]",
            svc.user or "[dim]-[/]",
            svc.path or "[dim]-[/]",
            _ssl_cell(svc, now),
            _git_cell(svc),
        )
    return table


@app.command()
def scan(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    fixture: Annotated[
        Optional[Path],
        typer.Option(help="Scan a fixture directory instead of this machine (development).",
                     exists=True, file_okay=False),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log collector details.")] = False,
) -> None:
    """Detect the sites and services running on this server (read-only)."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.ERROR,
                        format="%(levelname)s %(name)s: %(message)s")
    env: Env = ctx.obj if fixture is None else Env(fixture)
    result = run_scan(env.host, env.config)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(render_table(result))
    for warning in result.warnings:
        err_console.print(f"[yellow]warning:[/] {warning}")


# -- services: logs and actions ---------------------------------------------


def resolve_service(env: Env, query: str) -> Service:
    result = run_scan_with_meta(env.host, env.config, env.db)
    exact = [s for s in result.services if s.id == query]
    if exact:
        return exact[0]
    q = query.lower()
    matches = [s for s in result.services
               if q in s.id.lower() or q == s.name.lower() or any(q in d for d in s.domains)
               or (s.unit and q == s.unit.lower())]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        fail(f"no service matches {query!r} (see `mas scan`)")
    fail(f"{query!r} is ambiguous: " + ", ".join(s.id for s in matches))
    raise AssertionError  # unreachable


@app.command()
def logs(
    ctx: typer.Context,
    service: Annotated[str, typer.Argument(help="Service id, domain or name.")],
    lines: Annotated[int, typer.Option("--lines", "-n", min=1, max=2000)] = 100,
) -> None:
    """Show the recent logs of a service."""
    env: Env = ctx.obj
    svc = resolve_service(env, service)
    for source in get_logs(env.host, svc, lines):
        console.rule(f"[bold]{source.name}")
        if source.error:
            err_console.print(f"[yellow]{source.error}[/]")
        for line in source.lines:
            console.print(line, markup=False, highlight=False, soft_wrap=True)


def _action(ctx: typer.Context, service: str, action: str, yes: bool) -> None:
    env: Env = ctx.obj
    svc = resolve_service(env, service)
    if action not in available_actions(svc):
        allowed = ", ".join(available_actions(svc)) or "none"
        fail(f"{action} is not available for {svc.id} ({svc.manager.value}); allowed: {allowed}")
    if action in CONFIRM_ACTIONS and not yes:
        typer.confirm(f"{action} {svc.display_name or svc.name} ({svc.manager.value})?", abort=True)
    try:
        result = run_action(env.host, svc, action)
    except ActionError as exc:
        env.db.audit(actor(), action, False, svc.id, detail=str(exc))
        fail(str(exc))
        return
    env.db.audit(actor(), action, result.ok, svc.id, result.command, result.output)
    if result.output:
        console.print(result.output, markup=False, highlight=False)
    if not result.ok:
        fail(f"{action} failed")
    console.print(f"[green]✓[/] {action} {svc.id}")


YesOption = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")]
ServiceArg = Annotated[str, typer.Argument(help="Service id, domain or name.")]


@app.command()
def start(ctx: typer.Context, service: ServiceArg, yes: YesOption = False) -> None:
    """Start a service."""
    _action(ctx, service, "start", yes)


@app.command()
def stop(ctx: typer.Context, service: ServiceArg, yes: YesOption = False) -> None:
    """Stop a service (asks for confirmation)."""
    _action(ctx, service, "stop", yes)


@app.command()
def restart(ctx: typer.Context, service: ServiceArg, yes: YesOption = False) -> None:
    """Restart a service (asks for confirmation)."""
    _action(ctx, service, "restart", yes)


@app.command()
def reload(ctx: typer.Context, service: ServiceArg, yes: YesOption = False) -> None:
    """Reload a service."""
    _action(ctx, service, "reload", yes)


@app.command()
def history(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 30,
    service: Annotated[Optional[str], typer.Option("--service", "-s", help="Only this service id.")] = None,
) -> None:
    """Show the audit log (actions, logins, edits)."""
    env: Env = ctx.obj
    table = Table(header_style="bold")
    for col in ("When", "Who", "Service", "Action", "Result"):
        table.add_column(col)
    for row in env.db.history(service, limit):
        table.add_row(row["at"].replace("T", " ")[:19], row["actor"], row["service_id"] or "-",
                      row["action"], "[green]ok[/]" if row["ok"] else "[red]failed[/]")
    console.print(table)


@nginx_app.command("reload")
def nginx_reload_cmd(ctx: typer.Context) -> None:
    """Run `nginx -t`, then reload nginx only if the configuration is valid."""
    env: Env = ctx.obj
    result = nginx_reload(env.host)
    env.db.audit(actor(), "nginx-reload", result.ok, None, result.command, result.output)
    if result.output:
        console.print(result.output, markup=False, highlight=False)
    if not result.ok:
        fail("nginx was not reloaded")
    console.print("[green]✓[/] nginx reloaded")


# -- users / 2FA ---------------------------------------------------------------


def _prompt_password() -> str:
    while True:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
        problem = auth.check_password_strength(password)
        if problem is None:
            return password
        err_console.print(f"[yellow]{problem}[/]")


def _enroll_totp(db: Database, user_id: int, username: str) -> bool:
    secret = auth.new_totp_secret()
    uri = auth.provisioning_uri(secret, username)
    console.print("\n[bold]Two-factor authentication[/]")
    console.print("Scan this QR code with Google Authenticator, Aegis, 1Password, Bitwarden…\n")
    auth.qr_terminal(uri)
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    console.print(f"\nOr enter this key manually: [bold]{grouped}[/]\n")
    for _ in range(3):
        code = typer.prompt("6-digit code shown by the app")
        step = auth.match_totp(secret, code)
        if step is not None:
            db.update_user(user_id, totp_secret=secret, totp_pending=None, totp_last_step=step)
            console.print("[green]✓[/] two-factor authentication enabled")
            return True
        err_console.print("[yellow]wrong code (check the phone's clock), try again[/]")
    err_console.print("[red]2FA not enabled.[/] Retry with `mas user 2fa " + username + "`.")
    return False


def _get_user(env: Env, username: str):
    user = env.db.get_user(username)
    if user is None:
        fail(f"no user {username!r} (see `mas user list`)")
    return user


@user_app.command("create")
def user_create(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="Login name.")] = "admin",
    no_2fa: Annotated[bool, typer.Option("--no-2fa", help="Skip two-factor setup (not recommended).")] = False,
) -> None:
    """Create the admin account, then set up 2FA with a QR code."""
    env: Env = ctx.obj
    username = username.strip().lower()
    if not auth.USERNAME_RE.fullmatch(username):
        fail("username: 2-32 chars, lowercase letters, digits, . _ -")
    if env.db.get_user(username):
        fail(f"user {username!r} already exists")
    user_id = env.db.create_user(username, auth.hash_password(_prompt_password()))
    env.db.audit(actor(), "user-create", True, detail=username)
    console.print(f"[green]✓[/] user {username} created")
    if not no_2fa:
        _enroll_totp(env.db, user_id, username)


@user_app.command("rename")
def user_rename(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="Current login name.")],
    new: Annotated[str, typer.Argument(help="New login name.")],
) -> None:
    """Change the login name of an account (password, 2FA and sessions are kept)."""
    env: Env = ctx.obj
    new = new.strip().lower()
    _get_user(env, username)
    if not auth.USERNAME_RE.fullmatch(new):
        fail("username: 2-32 chars, lowercase letters, digits, . _ -")
    if env.db.get_user(new):
        fail(f"user {new!r} already exists")
    env.db.rename_user(username, new)
    env.db.audit(actor(), "user-rename", True, detail=f"{username} → {new}")
    console.print(f"[green]✓[/] {username} is now {new} (the authenticator app keeps working; "
                  "its label shows the old name until you run `mas user 2fa`)")


@user_app.command("list")
def user_list(ctx: typer.Context) -> None:
    """List accounts."""
    env: Env = ctx.obj
    for u in env.db.list_users():
        state = "[green]2FA on[/]" if u["totp_secret"] else "[yellow]2FA off[/]"
        console.print(f"{u['username']}  {u['role']}  {state}  created {u['created_at'][:10]}")


@user_app.command("passwd")
def user_passwd(ctx: typer.Context, username: Annotated[str, typer.Argument()] = "admin") -> None:
    """Set a new password (signs out every session)."""
    env: Env = ctx.obj
    user = _get_user(env, username)
    env.db.update_user(user["id"], password_hash=auth.hash_password(_prompt_password()))
    env.db.delete_user_sessions(user["id"])
    env.db.audit(actor(), "password-change", True, detail=username)
    console.print("[green]✓[/] password changed")


@user_app.command("2fa")
def user_2fa(ctx: typer.Context, username: Annotated[str, typer.Argument()] = "admin") -> None:
    """Set up (or replace) the authenticator app of an account."""
    env: Env = ctx.obj
    user = _get_user(env, username)
    if user["totp_secret"]:
        typer.confirm("2FA is already enabled. Replace the authenticator?", abort=True)
    if _enroll_totp(env.db, user["id"], username):
        env.db.delete_user_sessions(user["id"])
        env.db.audit(actor(), "2fa-enable", True, detail=username)


@user_app.command("2fa-reset")
def user_2fa_reset(ctx: typer.Context, username: Annotated[str, typer.Argument()] = "admin") -> None:
    """Disable 2FA of an account (lost phone). Requires root on the server."""
    env: Env = ctx.obj
    user = _get_user(env, username)
    typer.confirm(f"Disable two-factor authentication for {username}?", abort=True)
    env.db.update_user(user["id"], totp_secret=None, totp_pending=None, totp_last_step=0)
    env.db.delete_user_sessions(user["id"])
    env.db.audit(actor(), "2fa-reset", True, detail=username)
    console.print(f"[green]✓[/] 2FA disabled. Run `mas user 2fa {username}` to set it up again.")


@user_app.command("delete")
def user_delete(ctx: typer.Context, username: Annotated[str, typer.Argument()]) -> None:
    """Delete an account."""
    env: Env = ctx.obj
    _get_user(env, username)
    typer.confirm(f"Delete user {username}?", abort=True)
    env.db.delete_user(username)
    env.db.audit(actor(), "user-delete", True, detail=username)
    console.print(f"[green]✓[/] user {username} deleted")


# -- health (phase 2.5) ------------------------------------------------------------

LEVEL_STYLE = {"error": "[bold red]error[/]", "warning": "[dark_orange]warn [/]", "info": "[cyan]info [/]"}


@app.command()
def doctor(
    ctx: typer.Context,
    no_dns: Annotated[bool, typer.Option("--no-dns", help="Skip DNS resolution checks.")] = False,
) -> None:
    """Check certificates, certbot renewal, nginx and DNS (read-only)."""
    from mas.health import run_checks

    env: Env = ctx.obj
    checks = run_checks(env.host, env.config, dns=not no_dns)
    if not checks:
        console.print("[green]✓[/] no problem found")
        return
    for c in checks:
        console.print(f"{LEVEL_STYLE[c.level]} [bold]{c.title}[/] [dim]({c.category})[/]")
        if c.detail:
            console.print(f"       {c.detail}", markup=False, highlight=False)
        if c.fix:
            console.print(f"       [green]fix:[/] {c.fix}", highlight=False)
    errors = sum(c.level == "error" for c in checks)
    console.print(f"\n{errors} error(s), {sum(c.level == 'warning' for c in checks)} warning(s)")
    if errors:
        raise typer.Exit(1)


# -- GitHub & deployments (phase 3) ------------------------------------------------

github_app = typer.Typer(help="GitHub App configuration.", no_args_is_help=True)
app.add_typer(github_app, name="github")


def _github_client(env: Env):
    from mas.github import GitHubApp

    if not env.config.github_app_id:
        return None
    pem = env.host.read_bytes(env.config.github_private_key_path)
    return GitHubApp(env.config.github_app_id, pem) if pem else None


def _deployer(env: Env):
    from mas.deploy import Deployer
    from mas.state import Scanner

    scanner = Scanner(env.host, env.config, env.db)
    return Deployer(env.host, env.config, env.db, lambda: _github_client(env), find_service=scanner.get)


@github_app.command("setup")
def github_setup(ctx: typer.Context) -> None:
    """Configure an existing GitHub App by hand (the dashboard can create one for you)."""
    import secrets as pysecrets

    from mas.config import save_section

    env: Env = ctx.obj
    console.print("Create the App on https://github.com/settings/apps/new (see README), then:")
    app_id = typer.prompt("App ID", type=int)
    slug = typer.prompt("App slug (github.com/apps/<slug>)")
    pem_file = Path(typer.prompt("Path to the downloaded private key (.pem)"))
    if not pem_file.is_file():
        fail(f"{pem_file} not found")
    secret = typer.prompt("Webhook secret (empty = generate one)", default="", show_default=False) or \
        pysecrets.token_urlsafe(32)
    env.host.write_text(env.config.github_private_key_path, pem_file.read_text(), mode=0o600)
    save_section(env.host, "github", {"app_id": app_id, "app_slug": slug,
                                      "private_key_path": env.config.github_private_key_path,
                                      "webhook_secret": secret})
    env.db.audit(actor(), "github-app-setup", True, detail=f"app {slug} ({app_id})")
    console.print(f"[green]✓[/] saved. Webhook secret to paste in the App settings: [bold]{secret}[/]")
    console.print("Restart Mas to apply: sudo systemctl restart mas")


@github_app.command("status")
def github_status(ctx: typer.Context) -> None:
    """Show the GitHub App and its installations."""
    from mas.github import GitHubError

    env: Env = ctx.obj
    gh = _github_client(env)
    if gh is None:
        fail("GitHub App not configured: Settings → GitHub in the dashboard, or `mas github setup`")
    try:
        console.print(f"App [bold]{env.config.github_app_slug}[/] (id {env.config.github_app_id})")
        for inst in gh.installations():
            console.print(f"  installed on {inst.get('account', {}).get('login')} (installation {inst['id']})")
    except GitHubError as exc:
        fail(str(exc))


@app.command("apps")
def apps_list(ctx: typer.Context) -> None:
    """List apps deployed from GitHub."""
    env: Env = ctx.obj
    table = Table(header_style="bold")
    for col in ("App", "Repository", "Domain", "Type", "Mode", "Live release", "Last deploy"):
        table.add_column(col)
    for a in env.db.list_apps():
        live = env.db.get_release(a["current_release"]) if a["current_release"] else None
        last = (env.db.list_releases(a["id"], 1) or [None])[0]
        table.add_row(a["id"], f"{a['repo']}@{a['branch']}", a["domain"] or "-", a["type"], a["mode"],
                      f"#{live['id']} {(live['sha'] or '')[:7]}" if live else "-",
                      f"#{last['id']} {last['status']}" if last else "-")
    console.print(table)


@app.command()
def deploy(ctx: typer.Context,
           app_id: Annotated[Optional[str], typer.Argument(help="App id (see `mas apps`).")] = None,
           all_apps: Annotated[bool, typer.Option("--all", help="Every app deployed from GitHub, one after "
                                                                "the other.")] = False) -> None:
    """Deploy the latest commit of an app's branch now (logs printed live)."""
    env: Env = ctx.obj
    if all_apps == bool(app_id):
        fail("give an app id, or --all")
    deployer = _deployer(env)
    targets = deployer.redeploy_targets() if all_apps else [app_id]
    if app_id and env.db.get_app(app_id) is None:
        fail(f"unknown app {app_id} (see `mas apps`)")
    failed = []
    for target in targets:
        if all_apps:
            console.print(f"[bold]== {target}[/]")
        rid = env.db.create_release(target, "manual", actor())
        if not deployer.run(rid, echo=lambda line: console.print(line, markup=False, highlight=False)):
            failed.append(f"{target} (#{rid})")
    if all_apps:
        console.print(f"{len(targets) - len(failed)}/{len(targets)} app(s) deployed")
    if failed:
        fail(f"failed: {', '.join(failed)}")


@app.command()
def releases(ctx: typer.Context, app_id: Annotated[str, typer.Argument()],
             limit: Annotated[int, typer.Option("--limit", "-n")] = 15) -> None:
    """List the releases of an app."""
    env: Env = ctx.obj
    table = Table(header_style="bold")
    for col in ("#", "Status", "Commit", "Trigger", "By", "When", "Folder"):
        table.add_column(col)
    style = {"live": "green", "failed": "red", "building": "yellow", "queued": "yellow"}
    for r in env.db.list_releases(app_id, limit):
        table.add_row(str(r["id"]), f"[{style.get(r['status'], 'dim')}]{r['status']}[/]",
                      f"{(r['sha'] or '')[:7]} {r['message'] or ''}"[:60], r["trigger"], r["actor"] or "-",
                      r["created_at"][:16].replace("T", " "), "kept" if r["path"] else "-")
    console.print(table)


@app.command()
def rollback(ctx: typer.Context, app_id: Annotated[str, typer.Argument()],
             release_id: Annotated[Optional[int], typer.Argument(help="Release to restore (default: previous).")]
             = None, yes: YesOption = False) -> None:
    """Put a previous release back in production."""
    env: Env = ctx.obj
    app_row = env.db.get_app(app_id)
    if app_row is None:
        fail(f"unknown app {app_id}")
    if release_id is None:
        candidates = [r for r in env.db.list_releases(app_id, 50)
                      if r["path"] and r["id"] != app_row["current_release"] and r["status"] in ("superseded", "live")]
        if not candidates:
            fail("no previous release available")
        release_id = candidates[0]["id"]
    target = env.db.get_release(release_id)
    if not target or target["app_id"] != app_id or not target["path"]:
        fail("this release is not available anymore")
    if not yes:
        typer.confirm(f"Roll {app_id} back to release #{release_id}?", abort=True)
    rid = env.db.create_release(app_id, "rollback", actor(), target["sha"])
    env.db.update_release(rid, path=target["path"], message=f"Rollback to #{release_id}: {target['message'] or ''}")
    if not _deployer(env).run(rid, echo=lambda line: console.print(line, markup=False, highlight=False)):
        fail(f"rollback #{rid} failed")


# -- DNS providers -----------------------------------------------------------------

dns_app = typer.Typer(help="DNS providers (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun) and server IPs.",
                      no_args_is_help=True)
app.add_typer(dns_app, name="dns")


@dns_app.command("connect")
def dns_connect(
    ctx: typer.Context,
    provider: Annotated[str, typer.Argument(help="ovh, cloudflare, gandi, hetzner, digitalocean or porkbun")],
) -> None:
    """Connect a DNS provider account (credentials are verified, then stored encrypted)."""
    from mas.dns import load_providers, save_providers
    from mas.dnsproviders import PROVIDERS, DnsError, make_provider

    env: Env = ctx.obj
    if provider not in PROVIDERS:
        fail(f"provider must be one of {', '.join(PROVIDERS)}")
    cls = PROVIDERS[provider]
    console.print(f"[bold]{cls.label}[/]: {cls.help}")
    for name, url in cls.token_urls.items():
        console.print(f"  {'' if name == 'default' else name + ': '}{url}")
    creds = {}
    for f in cls.fields:
        if f.options:
            creds[f.name] = typer.prompt(f"{f.label} ({'/'.join(f.options)})", default=f.options[0])
        else:
            creds[f.name] = typer.prompt(f.label, hide_input=f.secret).strip()
    try:
        client = make_provider(provider, creds)
        account, zones = client.verify(), client.zones()
    except DnsError as exc:
        fail(str(exc))
        return
    connected = load_providers(env.host, env.config, env.db)
    connected[provider] = {"creds": creds, "account": account}
    save_providers(env.host, env.config, env.db, connected)
    env.db.audit(actor(), "dns-connect", True, detail=f"{cls.label} {account}")
    console.print(f"[green]✓[/] {cls.label} connected ({account}): {', '.join(zones) or 'no zone'}")


@dns_app.command("status")
def dns_status(ctx: typer.Context) -> None:
    """Show connected providers and the IPs used for new records."""
    from mas.dns import load_providers, server_ips
    from mas.dnsproviders import PROVIDERS

    env: Env = ctx.obj
    connected = load_providers(env.host, env.config, env.db)
    for ptype, entry in connected.items():
        console.print(f"{PROVIDERS[ptype].label}: {entry.get('account')}")
    if not connected:
        console.print("[dim]no DNS provider connected (mas dns connect <provider>)[/]")
    ipv4, ipv6 = server_ips(env.db, env.config)
    console.print(f"IPv4: {ipv4 or '[dim]not set (mas dns ip <address>)[/]'}")
    console.print(f"IPv6: {ipv6 or '[dim]-[/]'}")


@dns_app.command("ip")
def dns_ip(ctx: typer.Context, ipv4: Annotated[str, typer.Argument(help="Public IPv4 of this server.")],
           ipv6: Annotated[Optional[str], typer.Option(help="Public IPv6 (adds AAAA records).")] = None) -> None:
    """Set the public IPs that new DNS records point to."""
    from mas.dns import IPV4_KEY, IPV6_KEY
    from mas.ovh import OvhError, validate_ip

    env: Env = ctx.obj
    try:
        v4, v6 = validate_ip(ipv4, 4), validate_ip(ipv6, 6)
    except OvhError as exc:
        fail(str(exc))
        return
    env.db.set_setting(IPV4_KEY, v4)
    env.db.set_setting(IPV6_KEY, v6)
    console.print(f"[green]✓[/] new records will point to {v4}" + (f" and {v6}" if v6 else ""))


@dns_app.command("zones")
def dns_zones(ctx: typer.Context) -> None:
    """List the domains of every connected provider."""
    from mas.dns import zone_index

    env: Env = ctx.obj
    index, errors = zone_index(env.host, env.config, env.db)
    for zone, provider in sorted(index.items()):
        console.print(f"{zone}  [dim]{provider.label}[/]")
    for error in errors:
        err_console.print(f"[yellow]{error}[/]")


@app.command("domain")
def set_domain(
    ctx: typer.Context,
    app_id: Annotated[str, typer.Argument(help="App id (see `mas apps`).")],
    domain: Annotated[str, typer.Argument(help="e.g. test.example.com")],
    no_dns: Annotated[bool, typer.Option("--no-dns", help="Do not create the DNS record.")] = False,
    overwrite: Annotated[bool, typer.Option(help="Replace a record pointing elsewhere.")] = False,
) -> None:
    """Give an app a domain: DNS record at your registrar, then nginx + HTTPS (redeploys)."""
    from mas.deploy import DOMAIN_RE, domain_conflict, vhost_paths
    from mas.dns import server_ips, zone_index
    from mas.dnsproviders import DnsError, point_domain

    env: Env = ctx.obj
    domain = domain.strip().lower()
    target = env.db.get_app(app_id)
    if target is None:
        fail(f"unknown app {app_id}")
    if target["mode"] != "managed":
        fail("an adopted service keeps its own nginx vhost and domain")
    if not DOMAIN_RE.fullmatch(domain):
        fail(f"invalid domain: {domain}")
    conflict = domain_conflict(env.host, domain, set(vhost_paths(app_id)))
    if conflict:
        fail(f"{domain} is already served by {conflict}")
    if not no_dns:
        index, _ = zone_index(env.host, env.config, env.db)
        zones = [z for z in index if domain == z or domain.endswith("." + z)]
        if not zones:
            fail(f"no connected DNS zone for {domain} (use --no-dns if its DNS is managed elsewhere)")
        ipv4, ipv6 = server_ips(env.db, env.config)
        if not ipv4:
            fail("set the server IP first: mas dns ip <address>")
        zone = max(zones, key=len)
        try:
            result = point_domain(index[zone], zone, domain[: -len(zone)].rstrip("."), ipv4, ipv6, overwrite)
        except DnsError as exc:
            fail(f"{exc}" + ("" if overwrite else " (use --overwrite to replace it)"))
            return
        for change in result["changes"]:
            console.print(f"DNS ({result['provider']}): {change}")
    env.db.update_app(app_id, domain=domain)
    env.db.audit(actor(), "app-domain", True, detail=f"{app_id} → {domain}")
    rid = env.db.create_release(app_id, "manual", actor())
    if not _deployer(env).run(rid, echo=lambda line: console.print(line, markup=False, highlight=False)):
        fail(f"release #{rid} failed")


# -- server ----------------------------------------------------------------------


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Bind address (loopback only).")] = "127.0.0.1",
    port: Annotated[Optional[int], typer.Option(help="Port (default from config: 7070).")] = None,
    insecure_cookie: Annotated[bool, typer.Option(hidden=True, help="Allow cookies over plain HTTP (dev).")] = False,
) -> None:
    """Run the API and the dashboard (used by mas.service); an agent serves the cluster calls only."""
    import uvicorn

    from mas.api import create_app

    env: Env = ctx.obj
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if env.config.cluster_role == "agent":
        from mas.agent import create_agent_app
        from mas.cluster import ClusterError, validate_address
        try:  # an agent listens on the private network, never on a public address
            host = validate_address(env.config.cluster_address or "")
        except ClusterError as exc:
            fail(f"agent: [cluster] address in /etc/mas/config.toml: {exc}")
        uvicorn.run(create_agent_app(env.host, env.config, env.db), host=host, port=port or env.config.port,
                    proxy_headers=False, server_header=False, log_level="warning")
        return
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        fail("Mas only listens on loopback; expose it through nginx (deploy/nginx-mas.conf)")
    if insecure_cookie:
        env.config.cookie_secure = False
    application = create_app(env.host, env.config, env.db)
    uvicorn.run(application, host=host, port=port or env.config.port,
                proxy_headers=False, server_header=False, log_level="info")


agent_app = typer.Typer(help="This server as an agent of a cluster: no dashboard, driven by the primary server.",
                        no_args_is_help=True)
app.add_typer(agent_app, name="agent")


@agent_app.command("init")
def agent_init(
    ctx: typer.Context,
    address: Annotated[str, typer.Option(help="Private address of this server (VPC, WireGuard, Tailscale).")],
    name: Annotated[Optional[str], typer.Option(help="Name shown in the cluster (default: hostname).")] = None,
    restart: Annotated[bool, typer.Option(help="Restart mas to apply.")] = True,
) -> None:
    """Make this server an agent and print the join token to paste on the primary server."""
    from mas.cluster import Cluster, ClusterError, validate_address
    from mas.config import CONFIG_PATH, save_section
    from mas.deploy import Deployer

    env: Env = ctx.obj
    try:
        address = validate_address(address)
    except ClusterError as exc:
        fail(str(exc))
    save_section(env.host, "cluster", {"role": "agent", "address": address}, CONFIG_PATH)
    cluster = Cluster(env.host, env.config, env.db, Deployer(env.host, env.config, env.db, lambda: None))
    cluster.configure(name, address)
    token = cluster.create_token("primary")
    if restart:
        env.host.run(["systemctl", "restart", "mas"], timeout=60)
    console.print(f"[green]✓[/] {cluster.name()} is an agent, listening on {address}:{env.config.port}\n")
    console.print("On the primary server: Servers → Add a server")
    console.print(f"  address: [bold]http://{address}:{env.config.port}[/]")
    console.print(f"  token:   [bold]{token}[/]   (shown once)\n")
    console.print("[dim]Open the port on the private network only. To show the dashboard again: "
                  "sudo mas agent leave[/]")


@agent_app.command("token")
def agent_token(ctx: typer.Context) -> None:
    """Create another join token (the previous ones keep working until revoked)."""
    from mas.cluster import Cluster
    from mas.deploy import Deployer

    env: Env = ctx.obj
    cluster = Cluster(env.host, env.config, env.db, Deployer(env.host, env.config, env.db, lambda: None))
    console.print(cluster.create_token("primary"))


@agent_app.command("promote")
def agent_promote(
    ctx: typer.Context,
    force: Annotated[bool, typer.Option("--force", help="Even if the former primary still answers.")] = False,
    restart: Annotated[bool, typer.Option(help="Restart mas as the primary.")] = True,
) -> None:
    """The primary is out of service: make this server the primary, from the state it last sent."""
    from mas.cluster import Cluster, ClusterError
    from mas.deploy import Deployer

    env: Env = ctx.obj
    cluster = Cluster(env.host, env.config, env.db, Deployer(env.host, env.config, env.db, lambda: None))
    try:
        done = cluster.promote(force=force)
    except ClusterError as exc:
        fail(str(exc))
    console.print(f"[green]✓[/] {cluster.name()} is the primary now (epoch {done['epoch']}, "
                  f"replacing {done['former_primary'] or 'the former primary'})")
    console.print(f"  apps rebuilt here at restart: {', '.join(done['apps']) or 'none'}")
    for note in done["notes"]:
        console.print(f"  [yellow]![/] {note}")
    if done["domains"]:
        console.print("\n[bold]Point these domains to this server[/] (DNS A/AAAA records), then the certificates follow:")
        console.print("  " + ", ".join(done["domains"]))
    console.print("\nDashboard: through nginx as before (sudo bash deploy/install.sh --domain <dashboard domain>), "
                  "or right now: ssh -L 7070:127.0.0.1:7070 <you>@<this server>, then http://localhost:7070")
    if restart:
        env.host.run(["systemctl", "restart", "mas"], timeout=60)


@agent_app.command("leave")
def agent_leave(ctx: typer.Context, restart: Annotated[bool, typer.Option(help="Restart mas to apply.")] = True) -> None:
    """Back to a normal Mas with its dashboard (the copies it runs keep running until removed)."""
    from mas.config import CONFIG_PATH, save_section

    env: Env = ctx.obj
    save_section(env.host, "cluster", {"role": "primary"}, CONFIG_PATH)
    if restart:
        env.host.run(["systemctl", "restart", "mas"], timeout=60)
    console.print("[green]✓[/] this server is a normal Mas again (dashboard on 127.0.0.1, behind nginx)")


@app.command("self-update")
def self_update_cmd(
    ctx: typer.Context,
    from_archive: Annotated[
        Optional[Path],
        typer.Option("--from", exists=True, dir_okay=False, help="Install from a `git archive` tarball."),
    ] = None,
    no_restart: Annotated[bool, typer.Option("--no-restart", help="Do not restart mas.service.")] = False,
) -> None:
    """Update Mas: new source (git pull or --from tarball), reinstall, rebuild, restart."""
    from mas.selfupdate import UpdateError, self_update

    env: Env = ctx.obj
    if env.host.euid != 0:
        fail(f"Mas needs administrator rights for this. Run: [bold]{sudo_hint(sys.argv[1:])}[/]")
    try:
        self_update(env.config, from_archive, restart=not no_restart, log=console.print)
    except UpdateError as exc:
        fail(str(exc))
    from mas.banner import colors, render, version_from_source
    if colors():
        sys.stdout.write(render(version_from_source(env.config.src_dir) or __version__, subtitle="updated"))
    console.print("[green]✓[/] Mas updated")


def is_root() -> bool:
    return getattr(os, "geteuid", lambda: 0)() == 0


def sudo_hint(argv: list[str]) -> str:
    return "sudo mas " + " ".join(shlex.quote(a) for a in argv)


def run(argv: list[str] | None = None) -> None:
    """Entry point of the ``mas`` command: permission problems become a hint to use sudo."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        app(args=argv, prog_name="mas")
    except (PermissionError, sqlite3.OperationalError) as exc:
        denied = isinstance(exc, PermissionError) or any(
            s in str(exc) for s in ("unable to open database", "readonly database", "disk I/O error"))
        if is_root() or not denied:
            raise
        err_console.print(f"[red]error:[/] Mas needs administrator rights for this. Run: [bold]{sudo_hint(argv)}[/]")
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
