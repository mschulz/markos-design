---
title: MarkOS v0.3 M0-B Durable Storage
type: implementation-record
status: implemented-for-review
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - implementation
  - testing
---

# MarkOS v0.3 M0-B Durable Storage

## Result and review scope

M0-B adds explicit PostgreSQL storage to the independent runtime authorized by the owner. It preserves [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]]: no legacy application imports, PARA, Obsidian, Markdown database, DuckDB or personal-folder access. There is no RabbitMQ adapter, worker loop, gateway, source-object resolver or deployment.

The PR targets main and retains PR #10's independent M0-A commits as prerequisites. The storage commit can be reviewed separately from them; after PR #10 reaches main, the full diff narrows automatically. PR #9 remains the separate ADR-002 correction. Do not merge any PR into an old feature base as a promotion step. This work performs no merge.

## Review of PRs #9 and #10

PR #9 consistently replaces ADR-002's active MQTT statements with RabbitMQ and settles PostgreSQL/source-object/derived-Markdown authority. No runtime behavior changes and no CI checks were attached to that documentation-only PR.

PR #10 retains the correct independent contracts/configuration boundary. Review found its initial CI workflow failed before starting jobs because job-level env referenced `runner.temp`, which is unavailable there. Commit `6ff49b9` selects the environment in a run step through `GITHUB_ENV`; [the corrected hosted workflow passes](https://github.com/mschulz/markos-design/actions/runs/43144619198). Its implementation record and PR body record the fix. No remaining architecture blocker was found in this review; M0-A is not proof of durable storage or RabbitMQ delivery.

## Driver and database baseline

The independent lock adds exact `psycopg[binary]==3.3.8` and `psycopg-pool==3.3.3`, resolved from available registry artifacts and tested on Python 3.13.14. No root dependency or lock changes. Async connections with a maximum four connections per explicit store pool keep concurrent transactions separate. Autocommit outside explicit transactions avoids idle transactions; each operation sets transaction-local 5-second statement/1-second lock limits. Connection acquisition/connect budgets are five seconds. Pool connection errors and public storage exceptions redact DSNs, payloads and SQL.

Local integration uses PostgreSQL 17.11 in a new temporary cluster with no TCP listener and synthetic data only. CI uses a disposable PostgreSQL 17.11 service and synthetic credentials; this is a test baseline, not an assertion about the owner's Supabase project version or production plan. Verify the actual Supabase major, privileges, TLS, connection budgets and direct/session connection mode before cloud use. Database `session_user` must remain the authenticated service login; a shared administrative/pool identity is not an authorization substitute.

## Schema and atomic boundaries

`infrastructure/migrations/0001_durable_foundation.sql` defines:

| Records | Purpose and constraints |
|---|---|
| `service_identities` | Migration-managed authenticated login to service/owner/domain and synthetic publish/consume capability; private to the migration owner |
| `events` | Immutable scoped event ID, canonical wire bytes/body hash, operation fingerprint, publisher and expiry |
| `event_consumers` | Intended logical-consumer snapshot persisted with the event; later grants do not silently change old dispatch intent |
| `outbox` | Pending/leased/published storage state, DB-clock lease and increasing fence; publication state remains distinct from handoff/completion |
| `tasks`, `inbox` | Unique scoped business operation and consumer/event receipt, linked in one transaction |
| `task_results` | Scoped result-table boundary reserved for later execution; no completion API or business result is implemented here |
| `quarantine` | Bounded reason/hash/identity review record; no full sensitive payload, deduplicated across repeated identical conflicts |
| `schema_migrations` | Explicit migration ledger with exact SQL checksums |

`DurableStore.append(event)` atomically records event, intended consumers and outbox. Unknown/unauthorized source/owner/domain, invalid envelope or no intended consumer rolls back everything. Exact duplicate ID/body returns duplicate. Changed content under the same scoped event ID records quarantine without altering the original. The Python API serializes/validates canonical wire bytes; SQL also rejects unknown/malformed fields, duplicate JSON keys, invalid/nil identity, timestamp/payload/reference scope and unsupported synthetic event data.

`handoff(event)` resolves the authenticated consumer and stored publisher/event from PostgreSQL, compares the canonical wire hash and locks the intended-consumer dispatch row. It atomically creates inbox/task and marks handoff, returning only after commit. A duplicate receipt reuses its task. Distinct event IDs with the same scoped operation key and same business payload reuse one task; a changed business payload records operation conflict without a receipt/task. Expired new handoffs record review without a task. Existing committed receipts remain identifiable after expiry. Broker acknowledgement is not performed by this API.

`claim_outbox` uses bounded batches (1–10), DB-clock leases (1–300 seconds), row locks and SKIP LOCKED; concurrent claimers receive disjoint work. Reclaim increments the fence. `record_publication` accepts only the current unexpired fence and publisher identity. It is a storage primitive for M0-C to call after positive broker confirm/no return, not proof that any broker was contacted. Confirmed-work replay/reconciliation is deferred; retained event/consumer state is not deleted by this slice.

## Role and trust model

An explicit administrator provides the NOLOGIN `markos_runtime_client` group, a separate non-superuser migration/schema owner and dedicated non-admin service logins. Tests create these only on the dedicated local service; production identity provisioning is not run by this PR. Runtime logins must not inherit the schema owner or administrative roles.

The runtime group has schema usage, RLS-filtered SELECT and only scoped write-function execution. No direct INSERT/UPDATE/DELETE/TRUNCATE, schema creation, registry/ledger access, identity registration or grants are provided. Policies bind to authenticated `session_user`, not owner/domain parameters or custom session variables. Consumers see only their tasks/receipts; publishers see their own outbox. Cross-owner/domain rows are hidden. Security-definer functions validate capability and scope, use fixed safe search paths and schema-qualified relations, and have no public execution grants.

The migration/schema owner is a trusted privileged boundary; PostgreSQL table owners bypass RLS. Runtime initialization rejects superuser, BYPASSRLS, role-creator and schema-owner/member connections. This does not make the migration owner an ordinary runtime identity. See [PostgreSQL RLS semantics](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

## Migration boundary

`migrate(connection, explicit_directory)` is an explicit migration-role library API, not a worker/preflight startup action. It serializes migration runners with a database-local advisory transaction lock, checks contiguous ordered names and recorded checksums, rejects unknown applied versions/changed SQL, and commits DDL plus ledger together. Lock/statement budgets are five/30 seconds. A failure rolls back the migration transaction. Deployment/rollback procedures and future schema changes require separately reviewed migrations; do not edit this file after it is adopted.

No migration CLI is wired into `markos-runtime`; preflight stays validation-only and does not import storage or resolve DSNs. The storage API itself requires an explicit DSN with host/database/login. Configuration secret refs remain names until a future explicitly authorized adapter resolves them. [Psycopg async concurrency](https://www.psycopg.org/psycopg3/docs/advanced/async.html) and [PostgreSQL row locking](https://www.postgresql.org/docs/17/sql-select.html) inform the transaction/connection boundaries.

## Test evidence

The complete independent suite passes 145 tests: 111 contract/configuration/adapter-boundary tests plus 34 real PostgreSQL integration cases. Cases include transaction fault injection between writes, changed-ID-content quarantine, receipt/result identity, operation deduplication, duplicate concurrent handoffs, disjoint claims, locked-row skipping, expired/stale fences, expiry review, positive and negative owner/domain/consumer isolation, custom-variable/role spoof denial, direct DML/DDL denial, direct-SQL validation bypass attempts, checksum/idempotent migration checks and failed-DDL rollback.

Integration tests require an explicit `MARKOS_TEST_ADMIN_DSN` pointing at a dedicated disposable local test cluster. They create/drop only uniquely named `markos_test_*` databases and install test roles there; never supply a live/shared database. Missing DSN skips integration locally, but CI sets `MARKOS_REQUIRE_DB_TESTS=1`, making absent configuration fail rather than silently omit storage evidence. Hosted CI results are checked separately on the PR. The root archive remains unchanged and its 145-test preservation suite also passes, separately from these results. Independent/archive lock, Ruff and strict mypy gates pass (22/38 files respectively); independent formatting/schema checks pass. The built standalone wheel installs with eight hash-checked production dependencies, imports storage without connecting, and runs preflight outside the repository with no archived package, DuckDB or pytest installed. All 32 Markdown documents are classified, current/template links resolve, workflow YAML parses and diff whitespace checks pass.

## Remaining work

A04–A05 now have real database evidence. M0-C still owns AMQP confirms/returns, transport identity mapping, topology/permissions, timeout/channel recovery and commit-before-ack integration. M0-D owns task execution, worker leases/global slots, schedule state and business completion; M0-E owns source permission/deletion checks, replay/cleanup and 30/90-day behavior. Task-result schema is not a completed worker. No financial/source content, PostgreSQL cloud migration, external effects, merge, provisioning or deployment occurs here.
