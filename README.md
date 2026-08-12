# MarkOS

MarkOS is a local-first personal knowledge engine. Markdown remains the
canonical source of truth; applications and rebuildable indexes sit around it.

## M6 Graph analysis

This repository currently provides:

- a Typer command-line interface;
- environment and TOML configuration;
- structured console logging;
- a storage-independent Markdown document model;
- deterministic, recursive Markdown discovery within a configured vault;
- frontmatter, title, body, Markdown-link, and wikilink parsing; and
- an external DuckDB index rebuilt transactionally from Markdown; and
- deterministic read-only search over indexed paths, titles, and bodies; and
- conservative internal-link resolution with outgoing-link and backlink queries;
- a read-only HTTP API for health, search, documents, and graph navigation;
- graph health metrics and shortest directed paths over resolved links;
- automated tests across the foundation, ingestion, indexing, query, API, and analysis boundaries.

MarkOS does not modify a configured vault. Discovery and parsing are read-only;
index writes are restricted to an explicitly configured path outside the vault.

## Development

Allow the repository's direnv configuration once. It directs uv to a non-hidden
`venv` directory so macOS hidden-file flags cannot suppress Python's editable
package path:

```console
direnv allow
```

Install and run all checks:

```console
uv sync
uv run ruff check .
uv run mypy
uv run pytest
```

Run the CLI:

```console
uv run markos version
uv run markos doctor --vault /path/to/vault
uv run markos rebuild --vault /path/to/vault --index /path/to/index.duckdb
uv run markos search "knowledge engine" --index /path/to/index.duckdb
uv run markos links Vision.md --index /path/to/index.duckdb
uv run markos links Vision.md --incoming --index /path/to/index.duckdb
uv run markos graph --index /path/to/index.duckdb
uv run markos graph --index /path/to/index.duckdb --from Home.md --to Vision.md
uv run markos serve --index /path/to/index.duckdb
```

The API listens on `127.0.0.1:8000` by default and exposes interactive OpenAPI
documentation at `/docs`. It opens only the external index and never the vault.

Configuration can also be supplied in a TOML file:

```toml
[markos]
vault_path = "/path/to/vault"
index_path = "/path/to/index.duckdb"
log_level = "INFO"
```

Use it with `markos doctor --config /path/to/markos.toml`. Environment
variables `MARKOS_CONFIG`, `MARKOS_VAULT_PATH`, `MARKOS_INDEX_PATH`, and
`MARKOS_LOG_LEVEL` are also supported.
