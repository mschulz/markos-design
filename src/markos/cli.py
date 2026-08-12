"""Command-line interface for MarkOS."""

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from markos import __version__
from markos.logging import configure_logging
from markos.settings import LogLevel, Settings, SettingsError

app = typer.Typer(
    name="markos",
    help="A local-first personal knowledge engine built on Markdown.",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)


def _load_settings(config: Path | None, vault: Path | None, log_level: LogLevel | None) -> Settings:
    environment = dict(os.environ)
    if vault is not None:
        environment["MARKOS_VAULT_PATH"] = str(vault)
    if log_level is not None:
        environment["MARKOS_LOG_LEVEL"] = log_level

    try:
        settings = Settings.load(environ=environment, config_path=config)
    except SettingsError as error:
        error_console.print(f"[bold red]Configuration error:[/bold red] {error}")
        raise typer.Exit(code=2) from error

    configure_logging(settings.log_level)
    return settings


@app.command()
def version() -> None:
    """Show the installed MarkOS version."""

    console.print(f"MarkOS {__version__}")


@app.command()
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault path; overrides configuration."),
    ] = None,
    log_level: Annotated[
        LogLevel | None,
        typer.Option("--log-level", help="Logging level; overrides configuration."),
    ] = None,
) -> None:
    """Validate configuration and confirm that the vault can be read."""

    settings = _load_settings(config, vault, log_level)
    vault_path = settings.vault_path

    checks = Table(title="MarkOS diagnostics")
    checks.add_column("Check")
    checks.add_column("Result")
    checks.add_row("Configuration", "[green]OK[/green]")

    if not vault_path.exists():
        checks.add_row("Vault", "[red]Not found[/red]")
        console.print(checks)
        raise typer.Exit(code=1)
    if not vault_path.is_dir():
        checks.add_row("Vault", "[red]Not a directory[/red]")
        console.print(checks)
        raise typer.Exit(code=1)

    try:
        next(vault_path.iterdir(), None)
    except OSError:
        checks.add_row("Vault", "[red]Not readable[/red]")
        console.print(checks)
        raise typer.Exit(code=1) from None

    checks.add_row("Vault", "[green]Readable[/green]")
    checks.add_row("Write policy", "Read-only until explicitly approved")
    console.print(checks)
