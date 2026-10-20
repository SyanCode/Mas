"""``mas`` command line. Same modules as the API, no HTTP involved."""

from __future__ import annotations

import getpass
import ipaddress
import logging
import os
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

app = typer.Typer(help="Mas: discover and manage what already runs on this server.",
                  no_args_is_help=True, add_completion=False)
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
                fail(f"cannot open {self.config.db_path} ({exc}). Run with sudo?")
        return self._db


def fail(message: str, code: int = 1) -> None:
    err_console.print(f"[red]error:[/] {message}")
    raise typer.Exit(code)


def actor() -> str:
    return f"cli:{os.environ.get('SUDO_USER') or getpass.getuser()}"


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
    fixture: Annotated[Optional[Path], typer.Option(hidden=True, help="Fixture server (development).")] = None,
    db: Annotated[Optional[Path], typer.Option(hidden=True, help="Database path (development).")] = None,
) -> None:
    if version:
        console.print(f"mas {__version__}")
        raise typer.Exit()
    ctx.obj = Env(fixture, db)


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


@user_app.command("list")
def user_list(ctx: typer.Context) -> None:
    """List accounts."""
    env: Env = ctx.obj
    for u in env.db.list_users():
        state = "[green]2FA on[/]" if u["totp_secret"] else "[yellow]2FA off[/]"
        console.print(f"{u['username']}  {state}  created {u['created_at'][:10]}")


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


# -- server ----------------------------------------------------------------------


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Bind address (loopback only).")] = "127.0.0.1",
    port: Annotated[Optional[int], typer.Option(help="Port (default from config: 7070).")] = None,
    insecure_cookie: Annotated[bool, typer.Option(hidden=True, help="Allow cookies over plain HTTP (dev).")] = False,
) -> None:
    """Run the API and the dashboard (used by mas.service)."""
    import uvicorn

    from mas.api import create_app

    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        fail("Mas only listens on loopback; expose it through nginx (deploy/nginx-mas.conf)")
    env: Env = ctx.obj
    if insecure_cookie:
        env.config.cookie_secure = False
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    application = create_app(env.host, env.config, env.db)
    uvicorn.run(application, host=host, port=port or env.config.port,
                proxy_headers=False, server_header=False, log_level="info")


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
        fail("run with sudo")
    try:
        self_update(env.config, from_archive, restart=not no_restart, log=console.print)
    except UpdateError as exc:
        fail(str(exc))
    console.print("[green]✓[/] Mas updated")


if __name__ == "__main__":
    app()
