---
title: MarkOS v0.3 M0 Runbook
type: runbook
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# MarkOS v0.3 M0 Runbook

## Authorized environment

This procedure describes the synthetic GitHub-hosted CI harness in [[MarkOS v0.3 M0-F Lifecycle and Closeout]]. It is not an instruction to deploy on DigitalOcean/Supabase, attach accounts or run destructive tests on a personal/shared Docker host. Test project names are unique `markos-m0f-<run-id>-<attempt>` values; helpers verify hosted-runner/local-socket context and container ownership labels. Do not bypass those guards. No personal folder, archive package or production credentials enter the image.

## Validation and startup

1. Review all prerequisite changes and migrations. From `services/runtime`, select a separate `UV_PROJECT_ENVIRONMENT`, install with `uv sync --locked`, then run lock, Ruff/format, strict mypy, schema drift and pytest checks. Database/broker tests require dedicated disposable endpoints and their explicit require flags; skips are not evidence of integration success.
2. For lifecycle evidence, rerun the **Independent runtime contracts** GitHub Actions workflow for the review PR. The lifecycle job generates a short-lived test CA, starts isolated PostgreSQL/RabbitMQ, explicitly provisions test roles/queues, applies migrations 0001–0005, then builds only `services/runtime/Dockerfile`. It verifies strict TLS trust before starting roles. A worker never provisions its own authority or database schema.
3. The nine explicit test configurations live in `config/runtime/lifecycle/`. Application processes resolve only their named test secrets, check login-bound scope and connect to the private test dependencies. No real provider is configured. `cleanup_enabled` defaults false.
4. Private `/live`, `/ready` and `/metrics` listen on loopback inside each container. The `health` command checks readiness without displaying credentials. A failed dependency pass removes readiness; watchdog expiry exits the process so the supervisor can restart it. Readiness is not proof of business capacity or production availability.

## Failure investigation

Check the PR's job status and fixed JSON event codes first. Lifecycle diagnostics are bounded to recent synthetic logs. Do not print environment variables, secrets, connection strings, source bodies or arbitrary exception text. The dependency phase code distinguishes database, broker, consumer, topology audit and operation setup. A TLS failure must be fixed by correcting certificate/trust/hostname configuration, never disabling verification.

During dependency loss, outbox/tasks remain in PostgreSQL and consumers acknowledge only after durable handoff. Wait for reconnection/backoff and inspect durable result counts; do not acknowledge/delete broker work manually to make a check green. Pressure tests force RabbitMQ alarms, prove pending database work and then restore the configured test thresholds before waiting for backlog drain. Test timings and sampled resource use are fixture evidence only.

SIGTERM removes readiness, drains bounded active work and closes resources. The process hard deadline is 10 seconds by default; Compose allows 15 seconds. A nonzero exit or a killed worker is recovered using database lease/generation fences. `unless-stopped` does not restart an intentional stop; explicitly start that owned test service. A Docker daemon restart is tested only on the ephemeral hosted runner. A real VM reboot, system service startup and monitoring placement need a separately authorized drill.

## Synthetic backup and restore

1. Stop all test application writers gracefully. Dependencies remain running. The controller leaves deliberately leased synthetic task/outbox/review work to exercise restore handling, then creates a bounded custom-format PostgreSQL dump and a SHA-256 manifest under ignored `infrastructure/.test-artifacts/`. Objects are a tiny in-memory synthetic backup fixture. No backups or credentials are committed/uploaded as artifacts.
2. Verify the persisted dump against the manifest before restore; a corrupted candidate must fail verification. Create a fresh isolated database, restore with the migration role, and call its private `markos_runtime.fence_restored_state()` **before** starting any writer. Do not reuse old task/dispatch/review claim tokens. The test proves old generations are rejected even when numeric fences match.
3. Check synthetic object content hashes and current deletion tombstones; exclude deleted originals from the recovered object set. The fixture proves filtering, not real cloud restore or erasure. A future older production snapshot must reapply deletions/holds from an independently preserved current deletion record; this fixture's dump already contains its tombstone.
4. Point newly started synthetic roles at the fresh database using an ignored Compose override. Verify a new generation, one restore-history record and one durable result per intended consumer. Verify all services become ready and logs contain no fixture passwords/payload markers. Never silently adopt a changed generation in a surviving process.
5. Finally remove only the unique owned test project/volumes. Keep evidence in the CI logs: commits, migration versions, result assertions and measured recovery timings. Temporary test backup files disappear with the runner.

## Future production recovery and alerts

Before any production data, choose object/backup storage, encryption/key custody, independent deletion/hold records, backup schedule/restore owner, approved RPO/RTO and a separately hosted notification route. Isolate or revoke all previous writers before restoring; a generation in one database cannot fence an old database or revoke external credentials. Reconcile unknown provider outcomes and approvals before enabling effects. Retention of MCP audit records is governed separately by [[ADR-005 Governed MCP Access for Agents]], not by the 30/90-day operational event windows.

`lifecycle.monitor.monitor_once` has a synthetic loopback-only primary/fallback notification algorithm; tests prove fallback when the primary returns failure and reject remote/redirect endpoints. It is not deployed, independently placed monitoring. Configure and prove real outage notification, provider privacy and escalation ownership through a separate review before production. Routine backups and baseline independent monitoring must precede production data; M7 later hardens these controls. GitHub remains canonical for reviewed procedures; secrets/runtime records remain outside GitHub.

The machine-readable closeout ledger is `config/runtime/m0-closeout.json`. It deliberately records `production_ready: false`. See [[MarkOS v0.3 Roadmap]] for the remaining gates. This runbook grants no merge or deployment authorization.
