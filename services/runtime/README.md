# Independent MarkOS runtime — M0-A / M0-B / M0-C

This standalone project provides strict synthetic contracts and validation-only
preflight. The explicit PostgreSQL and M0-C RabbitMQ libraries connect only when called with DSNs.
Preflight does not connect, resolve secrets or execute tasks; none of these components
installs/imports the archived root project or accesses notes. No PARA, Obsidian,
DuckDB, Gmail/provider integration or production mode is included. RabbitMQ
consumers persist task/review handoffs; they do not execute business workers.

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
M0-C adds exact aio-pika 10.2.3 plus locked aiormq/pamqp transport dependencies. This is a tested baseline, not a latest-
version or production-security claim. See the root M0-A record for decisions,
trust boundaries and validation.

Storage integration tests require `MARKOS_TEST_ADMIN_DSN` pointing at a dedicated
disposable local PostgreSQL 17 instance. They create/drop synthetic databases and
roles. Set `MARKOS_REQUIRE_DB_TESTS=1` to require them; without a DSN they skip,
while contract/configuration tests still run. No production DSN belongs in Git.
The root M0-B record defines migration roles, atomic boundaries and remaining work.

Real RabbitMQ tests additionally need explicit disposable loopback
`MARKOS_TEST_RABBIT_HTTP`, `MARKOS_TEST_AMQP_BASE` and synthetic admin credentials;
`MARKOS_REQUIRE_RABBIT_TESTS=1` makes missing broker configuration fail. CI runs
the digest-pinned broker and PostgreSQL together. Without a local broker those
cases skip explicitly. The root M0-C record documents topology/identity trust,
API construction, durable review, timeouts and remaining readiness gates.
