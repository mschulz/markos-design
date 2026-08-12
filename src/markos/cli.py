"""Command-line interface for MarkOS."""

import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from markos import __version__
from markos.analysis import GraphAnalysisError, analyze_graph, shortest_path
from markos.api import create_app
from markos.freshness import FreshnessError, check_freshness
from markos.index import KnowledgeIndexError, rebuild_vault_index
from markos.logging import configure_logging
from markos.markdown import MarkdownDiscoveryError, MarkdownParseError
from markos.query import (
    KnowledgeQueryError,
    backlinks,
    ingestion_issues,
    outgoing_links,
    search_index,
)
from markos.settings import LogLevel, Settings, SettingsError
from markos.sync import SyncError, sync_vault_index
from markos.watch import WatchError, watch_vault_index

app = typer.Typer(
    name="markos",
    help="A local-first personal knowledge engine built on Markdown.",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)


def _load_settings(
    config: Path | None,
    vault: Path | None,
    index: Path | None,
    log_level: LogLevel | None,
    *,
    require_vault: bool = True,
) -> Settings:
    environment = dict(os.environ)
    if vault is not None:
        environment["MARKOS_VAULT_PATH"] = str(vault)
    if index is not None:
        environment["MARKOS_INDEX_PATH"] = str(index)
    if log_level is not None:
        environment["MARKOS_LOG_LEVEL"] = log_level

    try:
        settings = Settings.load(
            environ=environment,
            config_path=config,
            require_vault=require_vault,
        )
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

    settings = _load_settings(config, vault, None, log_level)
    vault_path = _required_vault_path(settings)

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


@app.command()
def rebuild(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault path; overrides configuration."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    log_level: Annotated[
        LogLevel | None,
        typer.Option("--log-level", help="Logging level; overrides configuration."),
    ] = None,
) -> None:
    """Rebuild the external DuckDB index from Markdown."""

    settings = _load_settings(config, vault, index, log_level)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        summary = rebuild_vault_index(_required_vault_path(settings), settings.index_path)
    except (KnowledgeIndexError, MarkdownDiscoveryError, MarkdownParseError) as error:
        error_console.print(f"[bold red]Index rebuild failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    console.print(
        f"Indexed [bold]{summary.document_count}[/bold] documents and "
        f"[bold]{summary.link_count}[/bold] links; rejected "
        f"[bold]{summary.rejected_count}[/bold] documents into {summary.path}"
    )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Text to find in indexed documents.")],
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100, help="Maximum number of results."),
    ] = 20,
) -> None:
    """Search the external DuckDB index without opening the vault."""

    settings = _load_settings(config, None, index, None, require_vault=False)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        results = search_index(settings.index_path, query, limit=limit)
    except KnowledgeQueryError as error:
        error_console.print(f"[bold red]Search failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    if not results:
        console.print("No matching documents.")
        return

    table = Table(title=f"Search results for {query!r}")
    table.add_column("Path")
    table.add_column("Title")
    table.add_column("Snippet")
    for result in results:
        table.add_row(result.path, result.title, result.snippet)
    console.print(table)


@app.command()
def issues(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
) -> None:
    """Show documents rejected during the latest index rebuild."""

    settings = _load_settings(config, None, index, None, require_vault=False)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        results = ingestion_issues(settings.index_path)
    except KnowledgeQueryError as error:
        error_console.print(f"[bold red]Issue query failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    if not results:
        console.print("No ingestion issues.")
        return

    table = Table(title="Ingestion issues")
    table.add_column("Path")
    table.add_column("Error")
    for result in results:
        table.add_row(result.path, result.error)
    console.print(table)


@app.command()
def freshness(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault path; overrides configuration."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
) -> None:
    """Report Markdown files changed since the latest index rebuild."""

    settings = _load_settings(config, vault, index, None)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        report = check_freshness(_required_vault_path(settings), settings.index_path)
    except (FreshnessError, KnowledgeQueryError, MarkdownDiscoveryError) as error:
        error_console.print(f"[bold red]Freshness check failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    if report.is_current:
        console.print("Index is current.")
        return

    table = Table(title="Index is stale")
    table.add_column("Change")
    table.add_column("Path")
    for change, paths in (
        ("Added", report.added),
        ("Modified", report.modified),
        ("Deleted", report.deleted),
    ):
        for path in paths:
            table.add_row(change, path)
    console.print(table)
    raise typer.Exit(code=1)


@app.command()
def sync(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault path; overrides configuration."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild even when the index is current."),
    ] = False,
) -> None:
    """Synchronize the index only when source Markdown has changed."""

    settings = _load_settings(config, vault, index, None)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        result = sync_vault_index(
            _required_vault_path(settings),
            settings.index_path,
            force=force,
        )
    except (
        FreshnessError,
        KnowledgeIndexError,
        KnowledgeQueryError,
        MarkdownDiscoveryError,
        SyncError,
    ) as error:
        error_console.print(f"[bold red]Sync failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    if not result.rebuilt:
        console.print("Index is already current; no rebuild needed.")
        return

    summary = result.summary
    if summary is None:  # guaranteed by SyncResult when rebuilt=True
        raise RuntimeError("sync rebuild did not return a summary")
    console.print(
        f"Synchronized [bold]{summary.document_count}[/bold] documents and "
        f"[bold]{summary.link_count}[/bold] links; rejected "
        f"[bold]{summary.rejected_count}[/bold] documents ({result.reason})."
    )


@app.command()
def watch(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    vault: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault path; overrides configuration."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    poll_interval: Annotated[
        float,
        typer.Option("--poll-interval", min=0.05, help="Seconds between vault scans."),
    ] = 1.0,
    debounce: Annotated[
        float,
        typer.Option("--debounce", min=0.0, help="Quiet period before synchronization."),
    ] = 0.5,
    log_level: Annotated[
        LogLevel | None,
        typer.Option("--log-level", help="Logging level; overrides configuration."),
    ] = None,
) -> None:
    """Watch Markdown and keep the external index current until interrupted."""

    settings = _load_settings(config, vault, index, log_level)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    console.print("Watching for Markdown changes. Press Ctrl-C to stop.")
    try:
        summary = watch_vault_index(
            _required_vault_path(settings),
            settings.index_path,
            poll_interval=poll_interval,
            debounce=debounce,
        )
    except KeyboardInterrupt:
        console.print("Watcher stopped.")
        return
    except WatchError as error:
        error_console.print(f"[bold red]Watch failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    rebuild_label = "rebuild" if summary.rebuilds == 1 else "rebuilds"
    error_label = "error" if summary.errors == 1 else "errors"
    console.print(
        f"Watcher stopped after {summary.sync_attempts} synchronization attempts; "
        f"{summary.rebuilds} {rebuild_label} and "
        f"{summary.errors} recoverable {error_label}."
    )


@app.command()
def links(
    identifier: Annotated[str, typer.Argument(help="Vault-relative document path.")],
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    incoming: Annotated[
        bool,
        typer.Option("--incoming", help="Show backlinks instead of outgoing links."),
    ] = False,
) -> None:
    """Show outgoing links or backlinks using the read-only index."""

    settings = _load_settings(config, None, index, None, require_vault=False)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        results = (
            backlinks(settings.index_path, identifier)
            if incoming
            else outgoing_links(settings.index_path, identifier)
        )
    except KnowledgeQueryError as error:
        error_console.print(f"[bold red]Link query failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    if not results:
        console.print("No links found.")
        return

    table = Table(title=f"{'Backlinks' if incoming else 'Outgoing links'} for {identifier}")
    table.add_column("Source")
    table.add_column("Target")
    table.add_column("Resolved path")
    for result in results:
        table.add_row(result.source_path, result.target, result.resolved_path or "Unresolved")
    console.print(table)


@app.command()
def serve(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    host: Annotated[
        str,
        typer.Option("--host", help="Interface on which to listen."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="TCP port on which to listen."),
    ] = 8000,
) -> None:
    """Serve the read-only HTTP API using only the external index."""

    settings = _load_settings(config, None, index, None, require_vault=False)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)
    uvicorn.run(create_app(settings.index_path), host=host, port=port)


@app.command()
def graph(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="DuckDB index path; overrides configuration."),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--from", help="Starting document for a directed path."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--to", help="Destination document for a directed path."),
    ] = None,
) -> None:
    """Show graph health or find a shortest directed path."""

    if (source is None) != (target is None):
        error_console.print(
            "[bold red]Usage error:[/bold red] --from and --to must be used together"
        )
        raise typer.Exit(code=2)

    settings = _load_settings(config, None, index, None, require_vault=False)
    if settings.index_path is None:
        error_console.print(
            "[bold red]Configuration error:[/bold red] index path is required; "
            "set MARKOS_INDEX_PATH or use --index"
        )
        raise typer.Exit(code=2)

    try:
        if source is not None and target is not None:
            path = shortest_path(settings.index_path, source, target)
            console.print(" → ".join(path))
            return
        report = analyze_graph(settings.index_path)
    except (KnowledgeQueryError, GraphAnalysisError) as error:
        error_console.print(f"[bold red]Graph query failed:[/bold red] {error}")
        raise typer.Exit(code=1) from error

    table = Table(title="Knowledge graph health")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Documents", str(report.document_count))
    table.add_row("Resolved links", str(report.resolved_link_count))
    table.add_row("Unresolved links", str(report.unresolved_link_count))
    table.add_row("Isolated documents", str(report.isolated_document_count))
    table.add_row("Weak components", str(report.weak_component_count))
    console.print(table)
    if report.isolated_documents:
        preview_limit = 20
        preview = report.isolated_documents[:preview_limit]
        console.print("Isolated: " + ", ".join(preview))
        remaining = len(report.isolated_documents) - len(preview)
        if remaining:
            console.print(f"… and {remaining} more isolated documents")


def _required_vault_path(settings: Settings) -> Path:
    if settings.vault_path is None:  # guarded by Settings.load(require_vault=True)
        raise RuntimeError("vault path was not validated")
    return settings.vault_path
