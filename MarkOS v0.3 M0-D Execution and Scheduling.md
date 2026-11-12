---
title: MarkOS v0.3 M0-D Execution and Scheduling
type: implementation-record
status: implemented-for-review
version: 1.1
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - implementation
  - testing
---

# MarkOS v0.3 M0-D Execution and Scheduling

## Result and prerequisite review

M0-D adds explicit synthetic execution and scheduling libraries to the independent `markos_runtime` package. PostgreSQL owns tasks, attempts/results, leases, global execution slots, schedule definitions/versions, occurrences and scan/control history. Scheduled events use the existing transactional outbox and RabbitMQ handoff; business execution never retains a broker delivery. No archived application, PARA, Obsidian, DuckDB or personal folder is used.

PR #12 at `22fbf25` was reviewed for confirms/returns, subscriber audit, producer authentication, commit-before-ack, cancellation-independent watchdogs, bounded recovery and additive migrations. No blocking correctness or architecture finding was identified. Its [CI passes all 194 tests](https://github.com/mschulz/markos-design/actions/runs/43151866637). Capacity, topology-change races and full broker-loss replay remain explicitly limited, rather than treated as completed acceptance. M0-D preserves migrations 0001/0002 exactly and adds 0003. The review branch targets main and preserves the #10/#11/#12 foundation history; the M0-D-only comparison starts at `22fbf25`. GitHub records those prerequisites merged by the owner during this work; no merge was performed by this implementation task. Parallel proposal branches #13/#14 are preserved and are not runtime prerequisites.

## Documentation reconciliation with main


The PR #15 documentation update incorporated main at `4d06339` without rewriting its original implementation commits. The AGENTS, authority-index, roadmap and README conflicts are reconciled alongside main's ADR-002 correction, governed MCP proposal, rules-first routing decisions and documentation CI. The specification retains those decisions with the M0-D implementation boundary. All runtime source/tests, locks and migrations are unchanged from the original PR #15 head `8286870`; no M0-E implementation was included in that PR #15 update. The combined inventory has 35 Markdown documents. That update changed only the review branch and did not merge the pull request or deploy anything.

## Synthetic execution boundary

`Worker(store).run_once()` explicitly claims and runs at most one task. Its fixed handler accepts only inline `foundation.noop.requested`; there is no handler/plugin/tool registry, provider connection, MCP access or consequential action. Success is a database-constructed `{kind: foundation.noop, event_id, effects: none}` result. A source reference goes to durable `source_unavailable` review until current access/deletion checks exist. Event expiry is rechecked at claim and finalization. No approval token, configuration label or body field enables a real action; full scoped approval-expiry acceptance remains a gate before real agents/tools.

Only authenticated identities with separately provisioned `can_consume` and `can_execute` may claim/finalize. New grants default false; runtime cannot change its registry. `can_schedule` is separately required with `can_publish` for schedule writes/scans. Integration fixtures grant these only to synthetic principals. RLS scopes readable tasks, attempts, results, reviews, definitions and history to authenticated service/owner/domain. Runtime has no direct DML, slot or configuration-grant access.

## Leases, retries and atomic completion

Every execution mutation shares a short PostgreSQL transaction lock and database clock. Two fixed global slot rows bound **valid execution leases to two across all service/owner/domain replicas in this database**, not two per process. No handler/network call occurs in that transaction. A lease claim increments the task fence and attempt, records the executor UUID and attempt history and reserves a slot atomically. A stale/expired fence, wrong executor or revoked identity cannot renew or finalize, even before another worker claims the task. Completion inserts one result, terminates the attempt/task and releases the slot in one commit. SQL uniqueness and existing operation/inbox identity prevent duplicate durable results.

The M0-D synthetic policy is a 30-second lease, 10-second execution deadline, three attempts and 1–300-second jittered retry availability. Renewal is explicit and limited to 300 seconds from attempt start; the short no-op worker does not need a background heartbeat. Connection/resource errors never become unrecorded success. Failed completion leaves a recoverable lease. Hard process exit leaves the committed claim for expiry/reclaim; takeover marks the old attempt abandoned. Repeated failures or crashed final attempts enter scoped `execution_reviews` without a fourth execution. Execution reviews are durable DB records; a human UI/notification bridge is later work, not an implied delivered broker review reference.

A timeout waits independently of handler cancellation, never records success and cancels/drains the handler within a bounded grace period. Incomplete cancellation disables that Worker object. Caller cancellation also leaves durable retry/lease recovery. This fixed no-op has no external effects. Two valid leases do **not** guarantee two physical computations under partitions or cancellation-resistant code: process isolation/termination and provider idempotency remain production gates. No claim of exactly-once external effects is made.

All storage transactions now set the session-local timezone to UTC. This preserves the database as the clock authority even when its default timezone is not UTC; it does not substitute the MacBook clock for lease/scheduler decisions.

## Persistent schedules and missed-run semantics

`ScheduleDefinition` is a strict versioned contract with stable ID, owner/domain, UTC start, IANA timezone, kind and explicit missed policy. The generated schema and `config/runtime/schedule.example.json` are checked. There are two supported schedule kinds: elapsed UTC intervals (60 seconds through 31 days), and one daily wall-clock occurrence at hour/minute. Arbitrary cron, weekday/monthly calendars and real job types are not accepted in this slice. Timezone data must be installed; unknown/unavailable zones fail closed.

Daily schedules persist a fold choice (`first` or `second`) and a gap policy (`shift_forward` by the daylight-saving gap). A repeated local time executes once at the selected instant. A nonexistent time moves forward by the gap, then subsequent dates return to the configured wall time. Intervals measure elapsed UTC seconds through daylight-saving changes. Start time must match the daily policy. The Python contract validates temporal semantics; SQL independently checks shape, scope, types and budgets. A corrupted/invalid definition fails the scan transaction closed and needs administrative correction.

Each scan locks at most ten due definitions with `SKIP LOCKED`, uses database time, and performs only bounded CPU planning before commit. A scan covers at most 31 elapsed days per definition; older debt remains visible for later explicit passes. At most 100 occurrences per definition are created in a pass. Policies are:

- **Catch up:** create the oldest eligible occurrences up to the persisted limit (default ten); retain the next overdue cursor and a pending-debt flag for another pass. Never silently truncate the remainder.
- **Coalesce:** create one latest eligible occurrence representing the covered range; audit collapsed/expired counts and advance beyond that range.
- **Skip:** audit missed occurrences; run only the latest occurrence within the persisted grace (default five seconds, 0–59 allowed), otherwise create no work for the covered range. This avoids a policy that skips even punctual jobs.

The per-job maximum age defaults to one day and is bounded to 30 days. Older covered occurrences are counted as skipped; no old provider effects run. Scan history persists the from/next range, covered/created/skipped counts, policy, age limit and remaining-debt flag. This per-schedule no-op policy does not implement M0-E's global event replay/retention/deletion rules or an external backlog alert.

Stable occurrence identity includes scheduler, owner/domain, schedule ID/version and original due UTC instant. One transaction inserts unique occurrence rows, immutable events, intended consumers/outbox entries and scan history, then advances the cursor. Failure at any step rolls the entire scan back. No RabbitMQ/network call occurs while schedule rows are locked. Task creation still follows the normal broker-to-inbox handoff, rather than bypassing transport.

Re-registering the exact definition is idempotent. Changed definitions require exactly the next version; immutable prior definitions/occurrences remain retained. A new version resets the cursor to its explicit start and creates distinct operations; choosing a past start deliberately requests the configured missed-run policy. Enable/disable changes are version-fenced and audited. Disable pauses future scans; it does not cancel already committed events/tasks. Resume recovers from the stored cursor. Cancellation of dispatched business work is a separate future contract.

## Evidence and acceptance

The required suite now has 256 tests: 169 unit/contract/configuration, 75 real PostgreSQL and 12 real broker/database cases. Local execution passes 244 with the 12 RabbitMQ cases explicitly skipped; hosted CI requires both services. One new broker case proves schedule → event/outbox → positive publication → durable inbox/task → synthetic completion. The fixed operation/result remains unique after another execution pass.

PostgreSQL tests cover competing claims, global cross-domain/owner leases, hard process exit/restart, stale/wrong-owner finalization and renewal, expiry at execution/completion, attempt exhaustion, unavailable claim/finalization functions, atomic result rollback, source-reference refusal, cancellation-resistant handlers, non-UTC sessions, concurrent/restarted scanners, event/outbox/cursor rollback, missed-run audit, immutable versions, enable/resume, RLS and direct-SQL schedule validation. Pure tests cover DST gap/fold/elapsed intervals, strict fields, stable occurrence identity, bounded debt and grace/age boundaries. Earlier broker regression cases still run.

A11 has synthetic lease/crash/fencing evidence; A12 has synthetic schedule concurrency/recovery/DST evidence. A13 has database-failure/no-effect evidence and a closed no-op execution boundary; real approval expiry remains unimplemented. A14–A20 remain later requirements, even where this slice adds one useful end-to-end case. The archived application's 145 tests and independent dependency/schema/build checks remain separate preservation/installation evidence. M0 is not complete.

## Remaining decisions and readiness

The subsequent [[MarkOS v0.3 M0-E Replay and Retention]] supplies synthetic retained-event/consumer recovery after actual disposable broker-volume loss, source observations/deletion/tombstones and 30/90-day cleanup/replay; real providers and erasure/restore remain gates. M0-F owns Compose/process lifecycle, startup/periodic loops, private health, monitoring/alerts, pressure and restart rehearsals. Real agents/MCP providers, scoped approvals, per-job production budgets and richer calendar schedules need separate reviewed contracts. The new APIs are explicitly invoked libraries; the CLI remains validation-only. No scheduler/worker daemon, cloud SQL, provisioning, deployment, merge, real account or personal-note change is performed.

## References

- [[MarkOS v0.3 M0 Implementation Plan]]
- [[MarkOS v0.3 M0-C RabbitMQ Transport]]
- [[MarkOS Cloud Deployment Architecture]]
- [PostgreSQL row/advisory locks](https://www.postgresql.org/docs/17/explicit-locking.html)
- [Python IANA zones and fold handling](https://docs.python.org/3.13/library/zoneinfo.html)
