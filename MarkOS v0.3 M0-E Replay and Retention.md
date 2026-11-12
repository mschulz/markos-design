---
title: MarkOS v0.3 M0-E Replay and Retention
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

# MarkOS v0.3 M0-E Replay and Retention

## Result and prerequisite review

M0-E adds explicitly invoked recovery/retention libraries to the independent runtime. PostgreSQL remains authoritative; RabbitMQ is transport, Markdown is a derived view, and the archived application is not used. No PARA, Obsidian, personal folder, email classification or MCP/tool execution is introduced.

PR #15 at `8286870` was reviewed for database-clock leases, fencing/global slots, atomic completion, bounded retry/review, schedule concurrency, stable occurrences and missed/DST policies. No blocking correctness or architecture issue was identified; its [256-test CI passes](https://github.com/mschulz/markos-design/actions/runs/43156429576). Real agents/approval dispatch and process lifecycle remain correctly deferred. M0-E preserves migration 0003 and adds 0004, retaining all earlier checksums.

The branch starts from current main `4d06339`, preserving merged MCP governance, rules-first M3 routing and ADR-002 corrections. PR #15 is a prerequisite: its commits were replayed only onto the new review branch to incorporate current main; its original branch/history was not rewritten. The M0-E-only comparison starts at `14932ae724e4e0944a5bad4b5086371372c8682d`. No PR is merged by this task.

## Recovery, identity and rate

`Recovery(store).reconcile_once(limit=10)` examines retained intended-consumer dispatch state, including **published** outbox entries. Missing handoffs are requeued with the original immutable event/operation IDs and subscriber snapshot. Existing durable inbox/tasks/results deduplicate recovered deliveries. A pending/leased dispatch is not repeatedly reset by reconciliation. A 60-second per-event probe delay after publication/replay avoids flooding subscribers during normal handoff delays; explicit reviewed requests can act sooner.

A database-wide reservation row spaces replay availability at ten events/second across replicas. Each recovery pass scans at most ten events (stricter than the proposed hundred-event batch ceiling), sharing ordinary publisher confirm/queue budgets. It does not perform broker calls while holding a transaction. Live topology auditing and positive confirms remain required. Broken bindings, blocked queues, unknown outcomes and unavailable services retain recoverable DB work; no new IDs or immediate requeue loop is used.

New capabilities `can_replay`, `can_manage_retention` and `can_approve_replay` default false and are provisioned separately. Authenticated service/owner/domain controls every public function; runtime cannot change registry, budgets, clocks, private legacy functions or tombstones. Synthetic fixtures explicitly grant test principals. Nothing enables a production identity.

## Age, expiry and suppressed effects

Versioned `ReplayRequest` contains event/permit identity and `suppress_effects=true`; it rejects replacement bodies, unknown fields, false/coerced flags and nil IDs. SQL replay history/permits enforce suppression independently. The worker remains a fixed inline no-op, with database-constructed `effects: none` results; replay enables no provider or consequential action.

Ordinary publication, handoff and execution enter durable scoped review when an event's original UTC occurrence is **more than 30 days** old. Exactly 30 days remains inside the boundary. Explicit event expiry always blocks work. Pre-publication authorization and post-confirm recording recheck database state, intended subscribers and source access; handoff/claim/finalization enforce the same gate. The publisher no longer decides expiry from its host clock.

An authorized reviewer can issue an expiring, one-use replay permit (default five minutes; one second through one hour allowed), bound to event ID/body hash, publisher, owner/domain and the immutable consumer set. Request-time redemption is atomic and rechecks current source/deletion/expiry. Permits cannot override those gates. Older missing handoffs can be recovered while that authority remains current; already-handed-off inline no-op tasks held solely for age/permit expiry may resume within the original attempt limit. No retry-exhausted/source-backed/expired/deleted job is automatically enabled. Permit expiry after publication still blocks later handoff/completion. This is synthetic replay authorization, not implementation of ADR-005's human approval gateway or real external-action approval expiry.

## Synthetic source observations and deletion

`observe_synthetic_source(source_id, fixture_bytes, services, accessible=...)` hashes explicit bounded fixture bytes, stores **no source bytes**, and records a scoped synthetic observation plus service grants. Replay/publication require the current checksum and access for the publisher and every intended consumer; handoff checks its own grant. Missing observations, denied access, changed checksums and deletion fail closed. Source-backed tasks remain non-executable in the M0-D no-op worker even with a valid observation.

These observations are a test boundary, not proof of a cloud provider's current permissions or object availability. A production source adapter must independently fetch/verify current metadata/bytes and authorization through a reviewed provider contract. No provider, bucket, vault or real source is contacted here.

Explicit source deletion revokes grants, expires permits and redacts all linked event envelopes in the owner's domain. Event deletion is publisher-scoped; the separately privileged source-deletion operation covers all publishers referencing that scoped source. Accepted payload bytes are removed immediately even for unfinished/held work, while minimal tombstones/audit and recovery identities remain. Related task fences/slots are invalidated; cached publications, stale completion and old queued deliveries cannot recreate work. Reimport of a tombstoned source/event ID is rejected/reviewed, including after graph cleanup.

Database checks and AMQP transmission are not atomic. A previously sent/in-flight broker copy can still contain earlier bytes until consumed/removed; consumers acknowledge only after durable rejection/review. This slice does not promise immediate broker/backup erasure, remote object deletion or deletion reapplication after an old restore. Tombstones must accompany the recovery set; production privacy/restore procedures remain a gate before real data.

## Operational retention and cleanup

The ADR-003 **operational-event** profile is implemented only for synthetic acceptance; final real-data/source/privacy retention is still proposed. It is separate from ADR-005's MCP audit/approval retention proposal.

`cleanup_once(limit=100, dry_run=True)` is explicit and defaults to a read-only plan. No automatic daily service is installed. Eligible candidates are filtered before the batch limit, so unresolved old records cannot starve later eligible cleanup.

- Retain payload/source references through at least 30 days from event creation and 30 days after all intended consumers/tasks become terminal, whichever is later. Positive publication alone is insufficient. Pending dispatch, incomplete tasks and unresolved recovery/quarantine/delivery/execution review hold data indefinitely under this cleanup policy.
- At that boundary, retire the wire payload and keep minimal event/dispatch/inbox/operation/task/result identity. At least 90 days from the latest creation, terminal transition or activity/attempt must pass before that graph can be purged. Explicit review resolution restarts the activity floor. Existing completed records receive a conservative migration-time floor.
- Recheck dependency/hold conditions and delete eligible results/attempts/inbox/tasks/occurrences/dispatch/event rows atomically. Shared-operation aliases keep their primary task until dependent receipts are released; a later pass can finish cleanup. Any failure rolls back the whole graph.
- Explicit privacy deletion takes precedence over payload holds; review/metadata can remain held for scoped resolution. Tombstone fingerprints, source observations, replay permits/history, retention audit and schedule/control audit are minimal ledgers and are **not age-purged by this increment**. Their final erasure/hold/retention policy is unresolved. “Metadata purged” in the API refers to the event/task dependency graph, not every audit ledger.

Database time controls policy; runtime accepts no caller-supplied clock. Only the isolated test DB migration role replaces the private clock function for boundary tests. There is no runtime time-travel setting. Dry-run leaves payload, identities and audit unchanged.

## Acceptance and evidence

The suite has **294 distinct runtime cases**: 177 unit/contract/configuration, 103 PostgreSQL, 13 broker/database and one dedicated broker-volume-loss case. The normal required hosted job runs 293; a second required job runs the volume case. Local execution passes 280 normal cases with 13 broker cases skipped explicitly; the Docker-only case is reserved for its dedicated CI job. Documentation has seven separate checker tests.

A14's dedicated test positively confirms an event, completes one subscriber, stops/removes only the owned disposable RabbitMQ container and its labelled named volume, and recreates an empty broker while PostgreSQL remains intact. It proves the old namespace is absent, restores reviewed topology/permissions, reconciles the missing subscriber and obtains both unique results with suppressed effects. The completed subscriber gets no second result. No wildcard, shared/live volume, cloud or production server is targeted. The Compose file is an acceptance harness, not M0-F's production lifecycle.

A15 tests exact 30/90-day boundaries, dry-run immutability, unfinished dispatch/tasks/leases, unresolved review, review-resolution floors, shared aliases, cleanup starvation and rollback. A16 tests denied/missing/changed/deleted synthetic sources, source reimport, stale publication/task fencing, queued deleted deliveries, one-use/expired/hash-bound replay permits and suppressed effects. Real source-provider/backup erasure and real action approvals remain unimplemented. The preserved archive's 145 regressions are separate evidence; full M0/A17–A20 operational acceptance is not complete.

## Explicit invocation and remaining work

Connect a non-admin `DurableStore` explicitly, then use `Recovery(store)` for observation, permits, requests, reconciliation, scoped deletion/review resolution and cleanup planning. Nothing connects, migrates or scans on import/preflight. The CLI remains validation-only. Examples/schema are under `config/runtime/`.

M0-F still owns startup/periodic loops, production Compose, private health, process isolation, pressure/quotas, monitoring/alerts and restore/restart drills. Before real data: choose source storage/provider and permission attestation, remote/broker/backup erasure and restore tombstone handling, final audit/hold retention and reviewed real-agent approvals. No merge, deployment, cloud provisioning, real provider/account, MCP activation or personal-note change is performed.

## References

- [[ADR-003 RabbitMQ Internal Event Bus]]
- [[ADR-005 Governed MCP Access for Agents]]
- [[MarkOS v0.3 M0 Implementation Plan]]
- [[MarkOS v0.3 M0-D Execution and Scheduling]]
- [Docker named-volume removal](https://docs.docker.com/reference/cli/docker/volume/rm/)
