# Independent MarkOS runtime — M0-A through M0-F

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

For preflight, only `--config` or `MARKOS_RUNTIME_CONFIG` selects configuration. Relative manifest
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

M0-D adds explicitly invoked `Worker.run_once()` and `Scheduler.register()` /
`scan_once()` libraries. They accept an explicitly connected non-admin store;
there is no `run` daemon command or startup migration. Migration 0003 adds
separately provisioned `can_execute`/`can_schedule` grants (default false),
DB-clock claims/fences, two global valid leases and immutable schedule history.
Only inline synthetic no-op work executes; source references enter review.
The strict interval/daily example is `config/runtime/schedule.example.json` at
repository root. See [[MarkOS v0.3 M0-D Execution and Scheduling]] for policies,
DST, retry/deadline limits, explicit invocation and production gaps.

M0-E adds `Recovery(store)` with explicit reconciliation/replay, one-use permits,
synthetic source observations, scoped deletion/review resolution and dry-run-first
cleanup. Grants default false; runtime has no clock override, real source adapter
or external effects. Migration 0004 preserves all prior checksums. See
[[MarkOS v0.3 M0-E Replay and Retention]] for source/privacy limits and API semantics.

The normal gate is `pytest --ignore=tests/integration/test_volume_loss.py --ignore=tests/integration/test_lifecycle_harness.py`.
The separate CI-only volume test needs its unique labelled disposable Compose
project and `MARKOS_REQUIRE_VOLUME_TEST=1`; it destroys only that broker volume.
Use neither the acceptance harness nor its credentials for a real service.

M0-F adds `serve-synthetic --confirm-synthetic --config <explicit service JSON>`; it runs only fixed test roles against isolated test targets, resolves named secrets, and exposes private loopback health/metrics. It does not replace connection-free preflight or enable production/provider execution. Migration 0005 requires generation tokens for lease operations and private migrator-only restore fencing. The dedicated GitHub-hosted lifecycle job proves process/dependency/pressure/daemon/fixture-restore behavior using a restricted independent image. See [[MarkOS v0.3 M0-F Lifecycle and Closeout]] and [[MarkOS v0.3 M0 Runbook]] for commands and open production gates. Never run the fault controller against a shared Docker host.

## M1-A validation-only control contracts

`markos-runtime preflight-control --config ../../config/runtime/control/config.example.json --event ../../config/runtime/control/chat-event.example.json` validates synthetic descriptors only. It resolves no secret or key and opens no connection. There is no control server/login, SQL migration, chat executor or MCP client. Existing M0 transport/store reject the planned chat types. A principal or intent digest is not authority. See [[MarkOS v0.3 M1-A Contracts and Identity]] and [[ADR-006 Control Plane Identity and Trust Boundary]].

The optional `control` extra is a locked compatibility profile, not a base dependency. In a separate environment use `uv sync --locked --extra control --no-dev`, then `uv run --no-sync python scripts/check_control_profile.py`. It exercises in-memory ASGI, ES256 primitives and template escaping without starting a listener. Keep the base test environment separate; its isolation checks require optional packages absent. Schemas now total 26, including 18 new control artifacts.

## M1-B explicit control persistence

`markos_runtime.control.store.ControlStore.connect(dsn)` is an explicitly invoked library with its own non-admin gateway role/allowlist. Its session methods persist trusted-verifier bindings but verify no token; M1-C supplies a fixture-only authentication boundary in the separate factory described below. Scoped registry/configuration, conversations/messages, activity and idempotency use narrow database functions and existing DTOs. `stage_message` returns held work only; it creates no broker dispatch, M0 outbox or runnable task. No API/server command is added. See [[MarkOS v0.3 M1-B Control Plane Persistence]] for permissions, session rotation/refresh, migration 0006, tests and remaining gates. That M1-B slice preserved dependency pins and 26 schemas; M1-C adds one API profile schema and an additive error code.


## Merged M1-B and M1-C review status

PR #20 is merged. [[MarkOS v0.3 M1-C Authenticated Shared API]] records the `6a87fb0` main review, green 533-case post-merge CI and a separately reviewable authenticated shared API. It adds an explicit fixture-only ASGI factory, browser/CLI PKCE session protocols, current signature/upstream/local authority checks, scoped registry/configuration/status/activity operations, private health and additive migration 0007. A separate loopback HTTPS profile keeps real accounts, chat and tools disabled. API metadata is not runtime knowledge authority; PostgreSQL/RabbitMQ and MCP/M3 decisions are preserved. Interactive web/CLI clients remain M1-E; chat promotion remains M1-D. Provider integration, key custody, scheduled secret cleanup, API packaging/process/restore and production gates remain open. No merge or deployment.


## Merged M1-C and M1-D review status

PR #21 is merged. [[MarkOS v0.3 M1-D Durable Test Agent Conversation]] records the `8311f01` main review, six green post-merge checks and a separately reviewable durable synthetic conversation. The explicit conversation API profile adds atomic message/event/outbox admission, fixed checksum acknowledgement, durable reply/completion/activity, scoped polling/cursors, cancellation and current-authority/epoch/fence checks. It shares PostgreSQL/RabbitMQ and the two global execution slots with the unchanged no-op path; migration 0008 is additive. M1-C/preflight configurations remain execution-disabled. Real identity adapters, interactive clients/SSE, governance/tools, scheduled cleanup and packaged process/restore/production gates remain separate work. No merge or deployment.


## Merged M1-D and M1-E review status

PR #22 is merged. [[MarkOS v0.3 M1-E Web and CLI]] records the `4cb4d7b` main review, six green post-merge checks covering 706 tests, and the separately reviewable client slice. It fixes activity cursor-purpose binding and adds an explicit fixture-only web profile with five pages, a shared-HTTPS CLI, exact typed write previews, private explicit CLI session storage and durable scoped SSE with shared 1/4/20 lease limits, current-authority checks and restore-aware cursors. Additive migration 0009 preserves 0001–0008; existing dependency pins and archived work remain intact. Browser acceptance is required in CI. Approvals/tools remain disabled for M1-F; packaged process/restore/operational closeout remains M1-G. Real accounts and deployment remain disabled. Earlier slice sections below/above are chronological evidence; this section describes the latest review state. No merge or deployment.
