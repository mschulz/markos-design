# MarkOS Project Memory

This file contains durable project context and working agreements for coding
agents. Keep it concise, current, and free of secrets.

## Project Purpose

- MarkOS is a local-first personal knowledge engine for Markdown vaults.
- Markdown is the canonical source of truth.
- DuckDB is an external, disposable, fully rebuildable index.
- MarkOS reads the vault but never edits, moves, renames, or deletes vault files.
- The HTTP API is read-only and opens only the external index.

## Current State

- Current release: `v0.2.0`.
- Milestones M0 through M10 are complete.
- M10 added conditional synchronization and the foreground `markos watch` command.
- The next software milestone is intentionally undefined until it is designed and accepted.
- The implementation repository is `https://github.com/mschulz/markos`.
- Architecture and roadmap decisions live in the separate `markos-design` repository.

## Repository Layout

- `src/markos/`: application code.
- `tests/`: pytest test suite.
- `README.md`: installation and everyday-use documentation.
- `pyproject.toml`: dependencies, tooling, package metadata, and local MarkOS configuration.
- `uv.lock`: locked Python dependencies.
- `.envrc`: directs uv and Python tooling to the non-hidden `venv` directory.

## Architecture Boundaries

- Discovery and parsing must remain read-only with respect to the Markdown vault.
- Reject index paths located inside the configured vault.
- Preserve the last valid index if synchronization or rebuilding fails.
- Serialize concurrent rebuilds.
- Keep query and HTTP operations read-only.
- Prefer composition of the existing discovery, freshness, indexing, and sync boundaries over
  creating alternate ingestion paths.
- Do not add vault-writing behavior without an explicit architectural decision and user approval.

## Python Environment

- Python 3.13 or newer is required.
- Dependencies and commands are managed with `uv`.
- Run `direnv allow` once after cloning when direnv is installed.
- `.envrc` sets `UV_PROJECT_ENVIRONMENT` and `VIRTUAL_ENV` to `$PWD/venv`.
- Use `direnv exec . uv ...` when running commands from an environment that has not loaded direnv.
- Do not recreate or switch back to `.venv`; macOS hidden-file behavior previously broke the
  editable package path there.

## Verification

Run the complete gate after code changes:

```console
direnv exec . uv lock --check
direnv exec . uv run ruff check .
direnv exec . uv run mypy
direnv exec . uv run pytest
```

- Mypy runs in strict mode over `src/markos` and `tests`.
- Ruff targets Python 3.13 with a 100-character line length.
- Add deterministic tests for new behavior.
- Watcher tests must use temporary vaults and injected timing rather than real-time sleeps.

## Everyday Operations

The repository-local TOML configuration identifies the user vault and the external index. Treat
the vault as read-only. Common commands are:

```console
direnv exec . uv run markos doctor --config pyproject.toml
direnv exec . uv run markos sync --config pyproject.toml
direnv exec . uv run markos watch --config pyproject.toml
direnv exec . uv run markos search "query" --config pyproject.toml
```

- The configured index is stored outside the vault at
  `/Users/markschulz/Library/Application Support/MarkOS/index.duckdb`.
- A current `sync` or initial watcher sync must leave the index byte-for-byte unchanged.
- `watch` is a foreground process; it is not installed as a system service.

## Working Agreements

- Inspect the worktree before editing and preserve unrelated user changes.
- Use focused branches prefixed with `codex/` unless the user requests otherwise.
- Do not push, tag, publish, or modify another repository unless the user requests it.
- Update tests and human-facing documentation when behavior changes.
- Keep this file limited to stable context. Put temporary task details in the current conversation,
  issue, or plan instead.
- Never store credentials, tokens, private note contents, or other secrets here.
