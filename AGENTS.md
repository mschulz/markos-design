# MarkOS Project Memory

This file contains durable project context and working agreements for coding
agents. Keep it concise, current, and free of secrets.

## Project Purpose

- MarkOS is a local-first personal knowledge engine for Markdown vaults.
- Markdown is the canonical source of truth.
- DuckDB is an external, disposable, fully rebuildable index.
- MarkOS v0.2 reads the vault but never edits, moves, renames, or deletes vault files.
- Accepted future milestones introduce opt-in, human-approved writes limited to configured
  workflows and paths.
- The HTTP API is read-only and opens only the external index.
- Markdown, not a vector database, is the durable semantic-memory layer.

## Current State

- Current release: `v0.2.0`.
- Milestones M0 through M10 are complete.
- M10 added conditional synchronization and the foreground `markos watch` command.
- M11 through M13 are accepted but not implemented:
  - M11: controlled PARA filing and a safe vault-write boundary.
  - M12: raw-document ingestion and grounded source notes.
  - M13: filesystem-native research and Markdown concept memory.
- M14 through M16 are accepted but not implemented:
  - M14: periodic knowledge maintenance, index/link/provenance health and repair proposals.
  - M15: native SwiftUI Mac app over the shared Python application services.
  - M16: user-approved background scheduling, notifications, signing and distribution.
- A read-only `markos para-audit` tool precedes M11 and assesses existing folders without
  changing the vault.
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

- Existing discovery, parsing, indexing, queries, and HTTP operations remain read-only.
- Reject index paths located inside the configured vault.
- Preserve the last valid index if synchronization or rebuilding fails.
- Serialize concurrent rebuilds.
- Keep query and HTTP operations read-only.
- Prefer composition of the existing discovery, freshness, indexing, and sync boundaries over
  creating alternate ingestion paths.
- Vault writes are disabled by default and require explicit configuration and user approval.
- Initial writes are limited to configured inbox and PARA paths. Do not overwrite existing files.
- Recheck source hashes before applying a proposed move or generated-note write.
- Keep vault-write permission separate from permission to send content to an external LLM.
- Preserve raw imported artifacts byte-for-byte and identify them by SHA-256.

## Markdown Memory Architecture

- Use ordinary text, metadata, wikilink, backlink, and graph exploration rather than embeddings
  or vector similarity.
- DuckDB may accelerate deterministic lexical and link queries, but it is not the knowledge store
  and must not become a vector database.
- The LLM researches progressively: search, read promising notes, follow links, collect evidence,
  and propose a Markdown synthesis.
- Maintain three provenance layers:
  1. immutable original artifacts under a concept folder's `raw/` directory;
  2. grounded source notes under `sources/`, with claims linked to original pages or locations;
  3. concept-memory Markdown that synthesizes multiple source and human notes.
- Mark generated source notes and concept memories clearly in frontmatter, including generation
  status, review status, source hashes, provider, and model where applicable.
- Generated concept memories are navigation and synthesis aids, not independent evidence.
- Future research must resolve generated claims back to source or human notes before relying on
  them, preventing generated-memory feedback loops.
- Label unsupported synthesis as inference, preserve contradictions, and never fabricate source
  references.
- Create and refresh concept memories through preview-and-confirm workflows with reviewable diffs.
- Invalidate derived extraction, summaries, and memories when recorded source hashes change.
- Treat semantic graph output as untrusted. Before import, require corpus-allowlisted source
  paths, resolved endpoints, valid confidence fields and complete source-location coverage.
- The local Ollama pilot rejected Qwen 2.5 7B for fabricated output. MarkOS source mapping lets
  the Qwen 3 14B control pass with complete provenance, but the genuine-note graph remains
  rejected because Graphify omitted mandatory relationship confidence scores. Do not scale yet.
- MarkOS-owned semantic preprocessing writes exact heading-aware chunks outside the source
  corpus, records source and chunk hashes plus original line ranges, and remaps only allowlisted,
  hash-verified chunk provenance. Broad chunk-derived locations must be labelled distinctly from
  provider-supplied chunk line locations.

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
- Do not move the existing `Roam` corpus wholesale into inbox. Audit and migrate it in reviewed
  batches from its current location because it is large and heavily linked.

## Working Agreements

- Inspect the worktree before editing and preserve unrelated user changes.
- Use focused branches prefixed with `codex/` unless the user requests otherwise.
- Do not push, tag, publish, or modify another repository unless the user requests it.
- Update tests and human-facing documentation when behavior changes.
- Keep this file limited to stable context. Put temporary task details in the current conversation,
  issue, or plan instead.
- Never store credentials, tokens, private note contents, or other secrets here.
