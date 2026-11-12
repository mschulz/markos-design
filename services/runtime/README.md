# Independent MarkOS runtime — M0-A

This standalone project provides strict synthetic contracts and validation-only
preflight. It does not install/import the archived root project, access notes,
connect to services, resolve secret values or execute tasks. No PARA, Obsidian,
DuckDB, Gmail/provider integration or production mode is included.

From this directory, use an isolated environment (ignore inherited `.envrc`):

```console
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv sync --locked
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv run markos-runtime preflight --config ../../config/runtime/runtime.example.toml --event ../../config/runtime/noop.example.json
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv run pytest
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv run ruff check .
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv run mypy
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv run python -m markos_runtime.schemas --output-dir ../../config/runtime/schemas
```

Only `--config` or `MARKOS_RUNTIME_CONFIG` selects configuration. Relative manifest
filenames resolve beside that file. No legacy env vars, `.env`, home folder or
repository root is discovered. Secret references are syntax-checked only;
preflight success does not prove credentials, broker ACLs or database access.

Python 3.13 is selected for this project's tested standard-library/typing baseline
and Pydantic compatibility, independently of the archive. The separate minimal
lock includes Pydantic plus development/build tools; transport/database libraries
are deliberately deferred to M0-B/M0-C. This is a tested baseline, not a latest-
version or production-security claim. See the root M0-A record for decisions,
trust boundaries and validation.
