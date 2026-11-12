---
title: MarkOS v0.3 M0 Implementation Plan
type: implementation-plan
status: proposed
version: 2.4
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# MarkOS v0.3 M0 Implementation Plan

## Scope and correction

Build an independent synthetic event-to-task foundation from [[MarkOS v0.3 Specification]], [[ADR-003 RabbitMQ Internal Event Bus]] and [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]]. The old import/reuse plan is superseded. No PARA note storage, Markdown database, DuckDB, legacy CLI/API/watcher or original dependency lock is a foundation requirement.

This plan defines reviewable implementation slices. M0-A supplies contracts/configuration, M0-B explicit PostgreSQL storage and M0-C explicit RabbitMQ adapters and M0-D synthetic execution/scheduling for review. No runnable business worker service exists, and nothing is merged or deployed by this work. The M0.0 import remains archival evidence. The new slice numbering below replaces the prior import-first sequence; use these names to avoid transferring old completion claims.

## M0-A implementation status

The owner authorized M0-A on 12 November 2026. [[MarkOS v0.3 M0-A Contracts and Configuration]] records its independent implementation and A01–A03 evidence, prepared for review. The acceptance table below remains the full M0 target: M0-A cases have unit/isolation evidence; the later sections record M0-B/M0-C evidence. No database/broker behavior or deployment is implemented by M0-A.

## M0-B implementation status

The owner authorized M0-B after review of PRs #9/#10. [[MarkOS v0.3 M0-B Durable Storage]] records the independent storage/migration/role API and 34 real PostgreSQL integration cases. A04–A05 have database evidence; the M0-C section records subsequent transport evidence. M0-B’s publication fence is a storage primitive, not broker confirmation. The runtime preflight remains validation-only.

## M0-C implementation status

The owner authorized M0-C after PR #11 review/green CI. [[MarkOS v0.3 M0-C RabbitMQ Transport]] records pinned RabbitMQ/aio-pika, live topology/identity checks, confirmed outbox publication, durable retry/review, manual ack after commit and independent watchdog/recovery. A06–A10 have transport evidence from real broker/database CI plus fault-injection unit tests. A11–A20 were later milestones at the M0-C boundary; broker volume-loss replay, source access and production operations are not completed here.

## M0-D implementation status

[[MarkOS v0.3 M0-D Execution and Scheduling]] records fixed no-op execution, global database lease slots/fencing, attempts/retry/review and atomic versioned interval/daily scheduling. A11/A12 have synthetic crash/concurrency/DST evidence; A13 has database-failure/no-effect evidence. Real approval expiry and full A14–A20 remain gates. No real agents/tools, daemon or deployment is delivered.

## Existing repositories and disposition

`markos-design` main at `a0ee6e434a2e3a8c900e50af6e9a71bc9afa6ade` preserves the imported v0.2 application through PR #5. Source repository `mschulz/markos` was inspected at `d5d46cbb231e95176a7d052553c176ebe8c805bb`; it is not the new implementation base and is not modified by this correction. Preserve both histories and existing work.

PR #6's M0.1 contracts/config were merged into a feature branch after its promotion had merged; they are not on main. PR #7 carries those artifacts plus legacy naming changes. Neither should be treated as the independent runtime scaffold. Review their event validation, topology and budget ideas as candidates; do not copy code or dependency assumptions automatically. No separate-repository push is needed while GitHub governance continues to select `markos-design`.

## Proposed independent layout

| Path | Purpose |
|---|---|
| `services/runtime/pyproject.toml`, `uv.lock` | Independent package/environment; supported Python/library choices justified and locked anew |
| `services/runtime/src/markos_runtime/contracts/` | Versioned event, service identity, subscription, task and configuration contracts |
| `services/runtime/src/markos_runtime/store/` | PostgreSQL transactions, scoped persistence, claims and migrations interface |
| `services/runtime/src/markos_runtime/transport/` | RabbitMQ publisher/consumer, topology validation and channel recovery |
| `services/runtime/src/markos_runtime/execution/` | Synthetic worker, scheduler, leases, reconciliation, replay and cleanup |
| `services/runtime/tests/` | Independent unit/integration/failure tests; no legacy import path |
| `config/runtime/` | Sanitized schemas, no-op examples and subscription/topology manifests |
| `infrastructure/migrations/`, `infrastructure/rabbitmq/` | Versioned SQL and reviewed topology/policies |
| `infrastructure/compose.m0-test.yml` | Isolated synthetic dependency harness only, not production deployment |

The scaffold must run without installing the root project or reading any personal folder. A minimal container/build context excludes legacy source/config. The new command name is proposed as `markos-runtime`; it must not dispatch into `markos.cli`.

## Ordered reviewable slices

| Slice | Required changes | Exit evidence |
|---|---|---|
| M0-A Independent contracts/config | Own project/lock/entry point; strict event and identity schemas; explicit runtime config and `foundation.noop`; review prior contract ideas | A01–A03; schemas/examples agree; no connection or folder discovery during preflight |
| M0-B Durable store | Ordered migrations; scoped events/consumer dispatch/outbox/inbox/tasks/results/quarantine; non-admin runtime roles | A04–A05; rollback/uniqueness/isolation and migration checks against isolated PostgreSQL |
| M0-C RabbitMQ transport | Locked client/image; exchanges, durable classic queues and permissions; publisher/consumer, topology auditor and watchdog | A06–A10; real confirms, returns, partial fan-out, commit-before-ack, reconnect and deadline evidence |
| M0-D Execution/scheduling | Durable synthetic worker, DB-clock leases/fences/global slots, persistent occurrences, retries and missed-run policy | A11–A13; competing workers, crash recovery, bounded catch-up and stale-owner rejection |
| M0-E Replay/retention | Retained event/consumer recovery, quarantine, deletion/source-access checks, cleanup and suppressed effects | A14–A16; controlled-clock boundaries and broker-storage-loss recovery |
| M0-F Lifecycle/CI/closeout | Isolated Compose harness, private health, redacted telemetry, clean-install CI and runbook | A17–A20; process faults, pressure, independent environment and explicit readiness gaps |

Each slice updates code/tests/docs in GitHub and gets review; no automatic merge. Preserve the archive without developing it. Legacy regression runs may detect accidental archive damage but cannot block new design choices merely because they differ from the old design.

## Contracts and state ownership

PostgreSQL commits immutable event identity/body hash and intended logical consumers with outbox state atomically. Events carry event_id, schema_version, event_type, owner_id, domain, trusted source, occurred_at UTC, correlation/causation/trace IDs, sensitivity, expiry and bounded payload or source reference. Reject unknown fields/types and mismatched identity scopes before dispatch. Proposed maximum body is 65,536 bytes.

Consumers atomically persist inbox identity `(consumer, owner_id, domain, event_id)` and durable task/result before manual acknowledgement. Same ID with changed body is a conflict; operation keys prevent double application. Long work executes from database tasks, not an unacknowledged broker delivery. Sources are object references; no filesystem or PARA model appears in these contracts.

Use separate migration/runtime roles and tested owner/domain RLS/grants based on trusted service identity. Claims use database time, bounded locks, leases and monotonic fencing. Do not hold transactions open across broker/provider calls. M0-B independently locks Psycopg 3.3.8 and pool 3.3.3; the archived driver/dependency graph is not inherited.

## Transport and operating profile

RabbitMQ is selected; retain ADR-003's proposed AMQP profile until verified: durable per-domain exchanges and per-service queues, persistent messages, mandatory publication and positive confirms. A return/nack/unknown outcome keeps the original event pending. Mandatory routing alone cannot prove every expected subscriber exists; audit the intended set. Retry original IDs with backoff and durable deduplication.

Proposed classic-queue flow: prefetch 10, two handoffs per process, 10-second active and 30-second total delivery-to-ack budget including waiting; 5-second DB statement/1-second lock timeout; close the dedicated channel on timeout/uncertain commit with a 2-second close budget then connection abort. No immediate requeue loop. Quarantine must be durable before acknowledgement. Reconnect must not create overlapping subscriptions.

ADR-003 owns the proposed broker/client pins, resource/queue limits, retention and recovery budget. M0-C records tested pins and synthetic transport behavior. Recheck support/security before production use; host capacity and retention remain unverified. Do not silently turn prior version recommendations into current verified facts.

## Acceptance matrix

A01–A03 have M0-A evidence; A04–A05 have M0-B PostgreSQL evidence; A06–A10 have M0-C broker/handoff evidence. M0-D supplies A11/A12 synthetic evidence and the A13 database-failure portion. Actual approval expiry and A14–A20 remain future requirements, even where a lower-level primitive has partial coverage. Unit fakes help prove decisions; real isolated PostgreSQL/RabbitMQ and process fault points must prove persistence, roles, locks and delivery behavior.

| ID | Concrete test/fault | Acceptance |
|---|---|---|
| A01 | Install/run only independent project in clean environment; block legacy package imports and set bogus legacy folder/config variables | Preflight runs without root package/lock, DuckDB, note tree or personal files; missing explicit runtime config fails closed |
| A02 | Contract boundary/unknown field/schema/invalid owner/source/domain, serialized body 65,536/65,537 bytes | Strict rejection, exact byte boundary and no dispatch on invalid input; no secret echo |
| A03 | Schema/example drift and topology/identity allowlist cases | Generated schemas match; only authorized service/owner/domain subscriptions allowed |
| A04 | Crash between event/outbox and inbox/task writes; repeat event and conflicting body | Whole transactions roll back; one scoped result; changed identity content enters conflict review |
| A05 | Non-admin cross-owner/domain reads/writes; competing row claims; migration/checksum failures | Isolation and least privilege hold; no double claim or partially applied migration |
| A06 | Unroutable publish, nack, lost confirm, missing one binding and queue-full partial fan-out | No false dispatch completion; original ID retry; independent subscribers each obtain durable work |
| A07 | Crash before commit, after commit before ack and during channel recovery | Redelivery creates one task/result; ack follows durable handoff; no overlapping subscriptions |
| A08 | Block DB handoff and saturate prefetch/semaphore; uncertain commit and failed channel close | Active/total deadlines close/abort appropriately; committed work resolved by inbox identity |
| A09 | Producer/consumer reconnect, unavailable broker and invalid permissions | Bounded backoff, pending outbox retained, topology restored before enabling publication, unauthorized access denied |
| A10 | Malformed/poison event, retry exhaustion and unavailable review queue | Durable scoped quarantine/reference before ack; no hot requeue or lost review record |
| A11 | Competing workers; kill lease owner; late completion after lease takeover | DB-clock fencing prevents stale finalization and duplicate synthetic business result |
| A12 | Scheduler outage across interval/DST boundaries and concurrent scans | Unique occurrences; explicit gap/fold behavior; bounded catch-up/coalesce/audited skip |
| A13 | Database unavailable during claims or finalization; stale approval | No unrecorded effects; recoverable task state; current authorization required |
| A14 | Delete isolated broker volume after positive confirms | Reconcile all missing intended-consumer handoffs from retained DB state, including confirmed events |
| A15 | Controlled clock at 30/90-day boundaries; pending work and unresolved quarantine | Proposed replay/dedup floors and unfinished-work holds respected; age gate routes review |
| A16 | Deleted/inaccessible source, changed checksum, expired approval, replay request | Block invalid replay, preserve deletion tombstone, suppress external side effects by default |
| A17 | Kill services, reboot isolated harness, interrupt shutdown | Restart/readiness/reconnect and lease recovery preserve durable results; no local desktop dependency |
| A18 | Message/queue/connection/channel limits, memory/disk pressure and backlog drain | Reject-publish/backpressure retains DB work; bounded memory and explicit alerts; measured capacity |
| A19 | Isolated DB/object restore and unavailable primary notification route | Validate recovery set/checksums, fence previous writers and prove independent alert/runbook design; production drill still separate |
| A20 | CI installs new project alone and runs synthetic end-to-end no-op workflow | New runtime passes independent gates; no live accounts/providers, archive imports, PARA or deployment |

## Readiness limits

Passing contracts alone does not complete M0. Production needs separately reviewed cost/security, source retention/deletion, backup/monitoring ownership and measured recovery targets. Gmail/control-plane/provider actions remain later milestones. No real source migration, deployment or provisioning is part of this plan.
