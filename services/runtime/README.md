# Independent MarkOS runtime — M0-A / M0-B

This standalone project provides strict synthetic contracts and validation-only
preflight. The explicit M0-B storage library connects only when called with a DSN.
Preflight does not connect, resolve secrets or execute tasks; neither component
installs/imports the archived root project or accesses notes. No PARA, Obsidian,
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
lock now includes Pydantic, Psycopg 3.3.8/binary, pool 3.3.3 and development/build tools;
transport libraries remain M0-C work. This is a tested baseline, not a latest-
version or production-security claim. See the root M0-A record for decisions,
trust boundaries and validation.

Storage integration tests require `MARKOS_TEST_ADMIN_DSN` pointing at a dedicated
disposable local PostgreSQL 17 instance. They create/drop synthetic databases and
roles. Set `MARKOS_REQUIRE_DB_TESTS=1` to require them; without a DSN they skip,
while contract/configuration tests still run. No production DSN belongs in Git.
The root M0-B record defines migration roles, atomic boundaries and remaining work.
