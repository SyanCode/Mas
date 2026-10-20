"""``mas`` command line."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from mas import __version__
from mas.correlator import scan as run_scan
from mas.host import FixtureHost, Host
from mas.models import ScanResult, Service, Status

SSL_WARN_DAYS = 14

app = typer.Typer(help="Mas: discover and manage what already runs on this server.",
                  no_args_is_help=True, add_completion=False)
console = Console()
err_console = Console(stderr=True)

STATUS_STYLE = {
    Status.RUNNING: ("●", "green"),
    Status.STOPPED: ("●", "red"),
    Status.ERRORED: ("●", "bright_red"),
    Status.UNKNOWN: ("○", "dim"),
}


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        console.print(f"mas {__version__}")
        raise typer.Exit()


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
            svc.name,
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
    host = FixtureHost(fixture) if fixture else Host()
    result = run_scan(host)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(render_table(result))
    for warning in result.warnings:
        err_console.print(f"[yellow]warning:[/] {warning}")


if __name__ == "__main__":
    app()
