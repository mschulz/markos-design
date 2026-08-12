# MarkOS

MarkOS is a local-first personal knowledge engine. Markdown remains the
canonical source of truth; applications and rebuildable indexes sit around it.

## M0 foundation

This repository currently provides:

- a Typer command-line interface;
- environment and TOML configuration;
- structured console logging;
- a storage-independent Markdown document model; and
- automated tests for the foundation.

MarkOS does not modify a configured vault in M0. The `doctor` command performs
read-only validation only.

## Development

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
```

Configuration can also be supplied in a TOML file:

```toml
[markos]
vault_path = "/path/to/vault"
log_level = "INFO"
```

Use it with `markos doctor --config /path/to/markos.toml`. Environment
variables `MARKOS_CONFIG`, `MARKOS_VAULT_PATH`, and `MARKOS_LOG_LEVEL` are also
supported.
