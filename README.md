# MarkOS

MarkOS is a local-first personal knowledge engine for a folder of Markdown
notes. Your Markdown files remain the source of truth. MarkOS reads them,
builds a disposable DuckDB index outside the notes folder, and provides search,
link navigation, graph analysis, and a read-only HTTP API.

MarkOS never edits, moves, renames, or deletes files in your Markdown vault.

## What you can do

- Search note paths, titles, and contents from the command line.
- Inspect outgoing links and backlinks.
- Check graph health and find paths between linked notes.
- Identify Markdown files that could not be parsed.
- Keep the index current automatically while you edit notes.
- Query the index from other local applications through a read-only API.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- macOS or Linux
- `direnv` is recommended, but optional

## Install from source

Clone the repository and install its locked dependencies:

```console
git clone https://github.com/mschulz/markos.git
cd markos
direnv allow
uv sync --locked
```

The repository's `.envrc` keeps the project environment in `venv`. If you do
not use `direnv`, set the environment location yourself before running `uv`:

```console
export UV_PROJECT_ENVIRONMENT="$PWD/venv"
uv sync --locked
```

Commands in this guide use `uv run markos`, so MarkOS does not need to be
installed globally.

## Quick start

### 1. Create a configuration file

Create a file such as `markos.toml`:

```toml
[markos]
vault_path = "/Users/alex/Documents/Notes"
index_path = "/Users/alex/Library/Application Support/MarkOS/index.duckdb"
log_level = "INFO"
```

Replace the example paths with your own. The vault is the folder containing
your Markdown notes. The index must be outside that folder; MarkOS will create
its parent directory when needed.

Validate the configuration and confirm that the vault is readable:

```console
uv run markos doctor --config /path/to/markos.toml
```

To avoid repeating `--config`, set it once in your shell:

```console
export MARKOS_CONFIG=/path/to/markos.toml
uv run markos doctor
```

The remaining examples assume `MARKOS_CONFIG` is set. You can add
`--config /path/to/markos.toml` to any command instead.

### 2. Create the first index

Run a conditional synchronization:

```console
uv run markos sync
```

If the index does not exist, MarkOS creates it. If it is already current,
MarkOS leaves it byte-for-byte unchanged. If Markdown has changed, MarkOS
rebuilds the index transactionally.

### 3. Search your notes

```console
uv run markos search "project planning"
```

Search is case-insensitive and checks vault-relative paths, titles, and note
bodies. It returns up to 20 results by default:

```console
uv run markos search "project planning" --limit 50
```

### 4. Keep the index current while you work

Start the foreground watcher in a terminal:

```console
uv run markos watch
```

The watcher performs an initial sync, checks for Markdown changes every second,
and waits for a short quiet period before syncing a burst of edits. Leave the
terminal open while working and press Ctrl-C to stop.

You can adjust its timing when needed:

```console
uv run markos watch --poll-interval 2 --debounce 1
```

The watcher is a foreground process, not an installed system service. Search
and API commands can safely read the index from other terminals while it runs.

## Everyday commands

### Check whether the index is current

```console
uv run markos freshness
```

The command reports added, modified, and deleted Markdown files. It exits with
status `0` when current and `1` when stale, which also makes it useful in shell
scripts.

### Synchronize once

Use this after editing notes when the watcher is not running:

```console
uv run markos sync
```

Force a rebuild even when no changes are detected:

```console
uv run markos sync --force
```

`rebuild` is also available when you explicitly want to reconstruct the index:

```console
uv run markos rebuild
```

### Inspect parsing problems

A malformed note does not prevent valid notes from being indexed. Show files
rejected during the latest rebuild with:

```console
uv run markos issues
```

Correct the reported Markdown or frontmatter, then run `markos sync` again.

### Explore links

Identifiers are exact paths relative to the vault and include the filename
extension.

Show links from a note:

```console
uv run markos links "Projects/MarkOS.md"
```

Show notes that link to it:

```console
uv run markos links "Projects/MarkOS.md" --incoming
```

MarkOS understands normal Markdown links and Obsidian-style wikilinks. Links
that cannot be matched conservatively to an indexed note are shown as
unresolved rather than guessed.

### Inspect the knowledge graph

Show document, link, isolation, and connected-component statistics:

```console
uv run markos graph
```

Find the shortest directed link path between two notes:

```console
uv run markos graph --from "Home.md" --to "Projects/MarkOS.md"
```

## Read-only HTTP API

Start the local API server:

```console
uv run markos serve
```

It listens on `http://127.0.0.1:8000` by default. Open
`http://127.0.0.1:8000/docs` in a browser for interactive API documentation.

Example requests:

```console
curl http://127.0.0.1:8000/health
curl 'http://127.0.0.1:8000/search?q=project&limit=10'
curl 'http://127.0.0.1:8000/documents/Projects/MarkOS.md'
curl 'http://127.0.0.1:8000/graph/backlinks?path=Projects%2FMarkOS.md'
curl http://127.0.0.1:8000/graph/summary
```

Use another port or listen address if required:

```console
uv run markos serve --host 127.0.0.1 --port 9000
```

The API opens only the external index. It does not open or modify the vault and
does not expose mutating HTTP methods.

## Configuration

MarkOS accepts settings from a TOML file, environment variables, or command-line
options. Command-line options take precedence over environment variables, which
take precedence over TOML values.

| Setting | Environment variable | CLI option |
|---------|----------------------|------------|
| Markdown vault | `MARKOS_VAULT_PATH` | `--vault` |
| DuckDB index | `MARKOS_INDEX_PATH` | `--index` |
| Log level | `MARKOS_LOG_LEVEL` | `--log-level` where supported |
| TOML file | `MARKOS_CONFIG` | `--config` |

Relative paths in a TOML file are resolved relative to that file. Supported log
levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.

You can bypass configuration for a one-off command:

```console
uv run markos sync \
  --vault "/Users/alex/Documents/Notes" \
  --index "/Users/alex/Library/Application Support/MarkOS/index.duckdb"
```

Search, link, graph, issue, and API commands need only the index path. Doctor,
freshness, sync, watch, and rebuild also need the vault path.

## Safety and recovery

- Keep `index.duckdb` outside the vault. MarkOS rejects unsafe index locations.
- The DuckDB file is derived data. Markdown remains canonical.
- A failed rebuild preserves the previous valid index transactionally.
- Concurrent rebuilds are serialized without persistent lock files.
- Deleting the index does not delete knowledge; run `markos sync` to recreate it.
- Back up the Markdown vault as you normally would. The index is not a backup.

If a command says the index is missing, incompatible, or unreadable, rebuild it:

```console
uv run markos rebuild
```

For detailed watcher or rebuild messages, temporarily enable debug logging:

```console
uv run markos watch --log-level DEBUG
```

Run `uv run markos --help` or `uv run markos COMMAND --help` for the complete
command reference.

## Development

Install the locked development environment and run all checks:

```console
direnv allow
uv sync --locked
uv run ruff check .
uv run mypy
uv run pytest
```
