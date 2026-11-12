---
title: Markdown Root Usage
type: user-guide
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - configuration
  - markdown
---

# Markdown Root Usage

MarkOS reads an ordinary folder of Markdown files and attachments. Its canonical
name is `markdown_root`. No Obsidian installation, metadata directory, API or
folder convention is required. Existing Markdown and wikilink parsing remains
available independently of an editor.

## Configure a standard folder

Supply explicit paths; MarkOS selects no personal folder by default. For a local
folder you choose, a configuration file can contain:

```toml
[markos]
markdown_root = "notes"
index_path = "state/index.duckdb"
log_level = "INFO"
```

Relative paths resolve against that configuration file. The index must stay
outside the Markdown root. The synthetic `config/legacy.example.toml` illustrates
the same naming but does not create or select any real notes.

```console
uv run markos doctor --markdown-root ./notes
uv run markos rebuild --markdown-root ./notes --index ./state/index.duckdb
uv run markos sync --markdown-root ./notes --index ./state/index.duckdb
uv run markos watch --markdown-root ./notes --index ./state/index.duckdb
```

The environment variable is `MARKOS_MARKDOWN_ROOT`. Existing CLI-over-environment-
over-TOML precedence is preserved. Read-only behavior, symlink exclusions,
relative document identifiers, external index placement and synchronization
semantics are unchanged. This PR moves or modifies no user notes.

## Python names

- `Settings.markdown_root` and `Settings.load(require_markdown_root=...)`.
- `rebuild_markdown_index`, `sync_markdown_index`, `watch_markdown_index`.
- `snapshot_markdown_root`, `MarkdownSnapshot`, `ParaAuditReport.markdown_root`.
- Existing discovery, parsing, freshness and audit functions use the
  `markdown_root` keyword.

## Compatibility while migrating

Deprecated `--vault`, `MARKOS_VAULT_PATH` and the TOML `vault_path` key continue
to work. Canonical names win within the environment or TOML layer; a higher-
priority legacy environment setting still overrides a lower-priority canonical
TOML setting. CLI aliases identify the same option, so if both flags are supplied
the parser's last-specified value wins; prefer one canonical flag.

Existing Python function names and the `vault_path` keyword are supported by
small deprecated adapters. Read-only `Settings.vault_path`, the old audit-report
constructor/property, `require_vault` and `VaultSnapshot` remain available.
Legacy function/keyword/property/config fallbacks emit DeprecationWarning;
the CLI alias, direct model input alias and type alias remain accepted silently.
Providing both Python root keywords to a function/report is rejected as ambiguous.
Do not remove compatibility names without a separately reviewed migration.

The isolated v0.3 runtime preflight ignores both folder-setting spellings. It
continues to load only explicit runtime configuration; this rename does not
grant runtime access to Markdown or source storage.

## History and review scope

The exact v0.2 README/project-memory snapshots, original specification/evaluation
records, M0.0 review/import evidence and import manifest keep their historical
terminology and hashes. They are provenance, not current configuration guidance.
Active code, examples, plans and memory guidance use Markdown-root terminology.

M0.1 had been merged into an earlier feature branch after its promotion PR had
merged. This PR targets main directly and retains that previously reviewed
M0.1 commit as an ancestor, alongside the terminology change. The rename commit
can be reviewed separately from that prerequisite; no PR is merged by this work.

## Validation

All 237 tests pass, including deprecated interfaces, configuration precedence,
read-only Markdown access and byte-identical no-op synchronization. Ruff, strict
mypy, lock-file validation and generated runtime schema checks pass. Dependency
versions, historical import records and archived source snapshots are unchanged.

## Related

- [[MarkOS v0.3 M0.1 Contracts and Configuration]]
- [[MarkOS v0.3 Specification]]
- [[Markdown Memory Architecture]]
