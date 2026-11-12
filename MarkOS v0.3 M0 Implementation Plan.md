---
title: MarkOS v0.3 M0 Implementation Plan
type: implementation-plan
status: proposed
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

# MarkOS v0.3 M0 Implementation Plan

## Decision

Build v0.3 M0 as a synthetic, durable event-to-task foundation in the canonical `markos-design` repository, after a separate history-preserving import of the existing `markos` implementation. Preserve its read-only vault/index functionality. Use the proposed RabbitMQ profile in [[ADR-003 RabbitMQ Internal Event Bus]] and PostgreSQL operational state; no Gmail account, model call, real notification or source-vault access is needed to prove M0.

This is a concrete proposal for review, not authorization to implement, provision, spend, merge or deploy. It concerns **v0.3 M0**, not the completed v0.2 M0. The original planning change ran no tests. The separately authorized M0.0 import now records its legacy/import checks in [[MarkOS v0.3 M0.0 Import Record]]; future runtime acceptance tests remain unexecuted.

## PR 3 consistency review

Reviewed [PR #3](https://github.com/mschulz/markos-design/pull/3) at `f9f519ea028cb188a982a5e9d231e90c983fc872` on 12 November 2026, including its six-file diff and referenced governance/hosting decisions. No review comments or submitted reviews were present at inspection.

| Finding | Consequence | Resolution in this documentation continuation |
|---|---|---|
| P2: classic-queue handoff has no consumer deadline | A live connection with a stalled database call can retain deliveries indefinitely; publisher timeout and heartbeat do not recover it | Add a 10-second active handoff budget and 30-second total unacknowledged age budget, including semaphore wait. Close the affected consumer channel on expiry; cancelling the consumer alone does not requeue |
| P2: section 4 requires owner/domain isolation, but section 5 event envelope lacks explicit owner identity | A domain-only contract does not define how to validate ownership or scope inbox/business keys | Require UUID `owner_id` in the envelope and owner-scoped uniqueness; compare it to trusted publisher/service scope, source access and database policy |
| Interpreter compatibility was still open | Could cause an unnecessary Python downgrade or migration detour | Existing implementation requires Python >=3.13 and pins 3.13; preserve it and test aio-pika there |

The proposed version/queue/resource numbers and 30/90-day policies agree across the ADR, specification, roadmap and cloud architecture. Publisher confirms, mandatory returns, partial fan-out, durable quarantine and broker-loss replay are consistent. Classic queues remain an explicit single-node tradeoff; there is no HA or exactly-once claim. No P0/P1 issue was found in this focused static review. Capacity, effective quotas, image digest and full source/privacy retention remain acceptance gates.

RabbitMQ 4.3+ acknowledgement timeouts apply only to quorum queues; [consumer cancellation does not requeue outstanding deliveries](https://www.rabbitmq.com/docs/consumers#acknowledgement-timeout). A separate [publisher deadline](https://www.rabbitmq.com/docs/confirms) is still required. Use the pinned aio-pika API's actual confirmation result, not generic truthiness; assert `Basic.Ack` and handle returns/nacks/unknown outcomes in tests.

## Inspected implementation baseline

The connected repository is [mschulz/markos](https://github.com/mschulz/markos), main `d5d46cbb231e95176a7d052553c176ebe8c805bb`. It had one branch, no open PRs and no `.github/workflows` tree at inspection. This is repository evidence, not an audit of unpushed local work.

| Existing component | Verified state | M0 treatment |
|---|---|---|
| [pyproject.toml](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/pyproject.toml), `.python-version`, `uv.lock` | Package 0.2.0, Python >=3.13 / 3.13, uv, strict mypy, Ruff, pytest; DuckDB, Pydantic, Typer, FastAPI and related knowledge tooling | Preserve interpreter/toolchain and lock baseline; add transport/database/test dependencies only in bounded implementation PRs |
| [settings.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/src/markos/settings.py) | TOML/env loader with vault/index paths; vault required by default | Leave legacy Settings behavior; create independent runtime settings that never load vault configuration |
| [domain.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/src/markos/domain.py) | KnowledgeDocument is a Markdown-path model, not an event/task model | Reuse validation style, not the model; add separate event/task contracts |
| [api.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/src/markos/api.py) | Read-only FastAPI app over the DuckDB index; no remote authentication layer | Preserve local API; do not expose it as the cloud control plane. M0 health is minimal/private, M1 owns authenticated user API/chat |
| [cli.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/src/markos/cli.py), logging | Existing Typer entry point and simple logging | Preserve commands; add a separate runtime entry point with redaction/structured events |
| [sync.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/src/markos/sync.py), [watch.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/src/markos/watch.py) | Filesystem sync and foreground polling with injected clock/stop signal | Preserve read-only behavior; reuse testability patterns, not the watcher as a durable scheduler |
| Semantic extraction, corpus and graph validation | Evidence-grounding functions and provider-boundary tests exist | Preserve; do not invoke providers or fold this subsystem into M0 |
| [tests/test_api.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/tests/test_api.py), [test_sync.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/tests/test_sync.py), [test_watch.py](https://github.com/mschulz/markos/blob/d5d46cbb231e95176a7d052553c176ebe8c805bb/tests/test_watch.py) | Tests assert no mutating HTTP routes, byte-identical no-op sync, external index and deterministic timing | Keep the whole legacy suite as a regression gate; no real vault |
| Missing from inspected tree/dependencies | RabbitMQ/aio-pika, PostgreSQL adapter/migrations, durable events/tasks/inbox/outbox, scheduler/leases, Compose, runtime CI | New M0 work, not an extension already implemented |

The source pyproject contains machine-specific vault/index defaults. Remove those active defaults during import and use synthetic examples; do not copy or follow the referenced live paths. The source AGENTS memory also records a synced-directory environment problem and older separate-repository/desktop decisions. Preserve its history, but reconcile active instructions with canonical governance; do not inherit its local paths or obsolete hosting assumptions.

## Import and preservation gate

The owner authorized M0.0 on 12 November 2026. Its import branch and validation
are recorded in [[MarkOS v0.3 M0.0 Import Record]]; review/merge remain pending.
This authorization covers the import and legacy regression checks only. M0.1–M0.7
and all deployment work remain unstarted. No runtime startup exists yet: M0.0
guards application import/config safety, and A02 remains a M0.1 requirement.

Prepare a dedicated `codex/` migration PR in `markos-design` before runtime changes:

1. Reinspect both remote heads/branches/open PRs and any actual checkout's dirty/untracked state. The inspected commit is the audit anchor, not permission to overwrite newer work.
2. Fetch the full source Git history into an isolated development checkout; propose a normal two-parent `--allow-unrelated-histories` merge, retaining the source commit as an ancestor. Do not squash or rewrite the source repository.
3. Keep canonical root design documents and their paths. Preserve the source README as `MarkOS v0.2 User Guide.md` and the source AGENTS context as historical `MarkOS v0.2 Project Memory.md`; reconcile root README/AGENTS/CLAUDE and merge ignore rules deliberately. Import source modules, tests, `pyproject.toml`, `uv.lock`, `.python-version` and reviewed `.envrc`; existing scaffold READMEs can coexist.
4. Remove active machine-specific defaults from imported pyproject; preserve original bytes in Git history, not new runtime examples. Keep cloud settings separate from `MARKOS_CONFIG` and vault environment variables.
5. Verify ancestry, a before/after file manifest and explained conflict resolutions; install/test in an isolated environment outside a synced folder. Do not relocate the user's current checkout or execute `direnv allow` against unreviewed content.
6. Run lock/Ruff/mypy/full pytest gates against synthetic temporary paths. Add a test that runtime startup does not load legacy settings or touch a vault even if legacy environment variables are present.
7. Merge only after owner review. Keep `markos` intact; do not archive, delete, push to it or claim migration complete from this planning PR. Retire or redirect its active workflow only through a separately reviewed change.

If import validation fails, stop before adding runtime code; retain both original repository heads and the pre-import lock as rollback anchors.

## M0 delivery slices and paths

All paths below are proposed in the canonical repository after import. Each slice is a focused PR with tests/docs and the previous slice green; no single large rewrite.

| Slice | Required changes | Exit evidence |
|---|---|---|
| M0.0 Import | History-preserving merge and reconciled README/AGENTS, package/tests/lock | Legacy gate, manifest and ancestry evidence |
| M0.1 Contracts/config | `src/markos/runtime/contracts.py`, `settings.py`, `ports.py`; `config/runtime.example.toml`, event JSON Schema/example and subscription manifest | Strict schemas, owner/domain/source validation, no vault access; profile values centralized and startup validates budgets |
| M0.2 Durable store | `runtime/db.py`, `store.py`; ordered SQL under `infrastructure/migrations/` | Transaction rollback, uniqueness, role isolation, concurrent claims and migration checksum tests |
| M0.3 Transport/handoff | `runtime/bus.py`, `publisher.py`, `consumer.py`, `quarantine.py`; reviewed broker definitions/policies | Confirms/returns/partial fan-out, durable handoff, stall deadlines and safe reconnect |
| M0.4 Execution/scheduling | `runtime/tasks.py`, `scheduler.py`, `reconcile.py`, `clock.py` | Database leases/fencing, occurrence uniqueness, bounded catch-up, idempotent synthetic worker |
| M0.5 Replay/cleanup | `runtime/replay.py`, `retention.py` and source-reference port | Retained confirmed-work recovery, age/deletion/approval gates, controlled-clock cleanup |
| M0.6 Lifecycle/verification | `runtime/main.py`, `health.py`, `telemetry.py`; `infrastructure/compose.m0-test.yml`, `rabbitmq/`, Dockerfile and runbook | Synthetic end-to-end harness, restart/pressure evidence, private health, backup/monitor hooks |
| M0.7 CI and closeout | `.github/workflows/ci.yml`, `tests/runtime/`, `tests/integration/`, README/roadmap evidence | Legacy/new gates and all M0 cases; measured limits and unresolved readiness gates recorded |

Keep the `markos` CLI behavior and local API intact; add `markos-runtime` mapped to `markos.runtime.main:main` rather than changing legacy command defaults. Proposed modes are publisher, consumer, worker, scheduler, reconciler and private health/diagnostics. M0's worker handles `foundation.noop` only, recording a synthetic business result. Registry/web chat, Gmail, classification, electricity and notifications are M1–M6.

Use Python 3.13 and uv; add exact `aio-pika==10.2.3` with locked transitives in M0.3. Propose Psycopg 3 async connections/pool with typed SQL in M0.2, not an ORM or Supabase browser SDK; select and lock the current supported patch then. Use a small explicit pool (initial max 4 per process, aggregate capped to the approved database plan), separate connections for concurrent transactions and database time for claims. Versioned SQL migrations run only with a migration role, never automatically from worker startup. [Psycopg concurrency](https://www.psycopg.org/psycopg3/docs/advanced/async.html), [pool lifecycle](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).

For CI integration, use an isolated pinned PostgreSQL service matching the intended Supabase major after inspection; do not assume Supabase runs the newest upstream release. Use the pinned RabbitMQ image with resolved digest. No production DSN, cloud credentials, Gmail token or provider secret belongs in CI.

## Contracts and durable state

EventEnvelope uses strict Pydantic validation, `extra=forbid`, UUID event/owner/correlation/trace IDs, allowed domain enum, schema version, allowlisted event/source identifiers, timezone-aware UTC occurred_at, explicit nullable root causation_id, sensitivity and optional business expiry. Require exactly one of bounded inline payload or access-controlled payload_ref. Enforce the 65,536-byte serialized body limit; validate publisher identity against owner/domain/source and map exchange/routing consistently.

| Proposed tables | Required constraints and behavior |
|---|---|
| `runtime_identities`, `identity_scopes` | Migration-managed service-role-to-owner/domain allowlist; runtime roles cannot grant themselves scope |
| `events`, `event_consumers` | Immutable event identity/hash and intended logical consumers captured transactionally; same ID with changed body is a conflict/quarantine, not silently deduplicated |
| `outbox` | Publication state, lease/fence, attempts, available_at, confirm/error; transactionally paired with event and required-consumer snapshot |
| `inbox`, `tasks`, `task_results` | Unique (consumer, owner_id, domain, event_id); unique scoped business-operation key; inbox and task/result commit together |
| `schedules`, `schedule_runs` | Owner/domain, version, timezone, policy, next_due; unique schedule/version/due-time occurrence; occurrence/task/outbox/next_due in one transaction |
| `worker_slots` and leases | Two database-managed worker slots VM-wide; monotonic fencing token and DB-clock expiry; stale owner cannot finalize or authorize effects |
| `quarantine`, `audit`, `deletion_tombstones` | Bounded/redacted diagnostics, original identity/reference and resolution; durable review reference outbox; deletion prevents future replay |
| `approval_refs`, `source_refs` | Minimal expiry/revocation and checksum/access/deletion test metadata, not the complete M1/M5 domain model |

Apply RLS and least-privilege grants keyed to database login identity and its migration-managed scopes. Do not trust a caller-supplied owner/domain or unrestricted session variable to confer access. Test publisher, consumer, replay and retention paths as non-admin roles. A dispatcher needing multiple scopes receives an explicit allowlist, not BYPASSRLS/service-role privileges. Migration/admin secrets never reach runtime/browser code.

Transactions use unique constraints plus bounded row locks; use `FOR UPDATE SKIP LOCKED` for competing queue-like claims and database time for leases. Release SQL transactions before broker/network calls; never wait on a publish confirmation while holding a schedule lock. Publisher claims and then sends; consumer commits and then acks. Either gap can duplicate delivery, so all completion and business-operation decisions re-read durable identity. [PostgreSQL locking guidance](https://www.postgresql.org/docs/current/sql-select.html).

Initial worker lease is 60 seconds with renewal every 20 seconds, claim batch at most 10 but never more tasks than acquired global slots, and two global worker slots. These are proposed synthetic-work defaults, not a generic LLM timeout. A late worker loses its fence and cannot finalize; a successor verifies operation state before proceeding. Consequential effects stay disabled in M0. Schedules support interval and wall-clock/cron expressions with IANA timezone, explicit DST gap/fold rules, catch-up/coalesce/audited skip, and a proposed maximum 100 missed occurrences or 24-hour catch-up age per scan. Truncation is recorded and escalated, not silently discarded.

## Consumer timeout and channel contract

Use one logical consumer per dedicated channel, prefetch 10 and a process-wide handoff semaphore of 2. Bound active handoff at 10 seconds and total delivery-to-ack age at 30 seconds including semaphore wait. Set transaction-local statement_timeout to 5 seconds and lock_timeout to 1 second. Watch total age independently of database coroutine progress; test a saturated prefetch queue as well as a single blocked statement.

On expiry or uncertain commit: do not ack; close the affected channel, with a proposed 2-second close budget then consumer-connection abort as fallback. Cancel old handler tasks and gate recovery to avoid two subscriptions; discard a suspect DB connection, reconnect with backoff, and resolve any committed result by inbox identity. Channel/connection closure may redeliver sibling work, so deduplication covers every delivery. These deadlines do not replace database completion verification, heartbeat or publisher's separate 10-second confirmation deadline.

## Acceptance matrix

All cases below are future requirements. Unit tests inject clock/jitter/store/bus faults. Integration tests use real isolated PostgreSQL/RabbitMQ containers and process kill points; test doubles alone cannot prove persistence, locks, ACLs or redelivery.

| ID and proposed test file | Setup/fault | Required assertion |
|---|---|---|
| A01 `test_import_regression.py` | Import baseline; temporary notes/index; run legacy suite | Source ancestry retained; local commands/API unchanged, no vault write, current sync byte-identical |
| A02 `test_runtime_settings.py` | Set legacy vault/config environment; start runtime with synthetic DSNs | No legacy config/vault access; missing credentials fail closed without printing secrets |
| A03 `test_event_contract.py` | Missing/forged owner, domain/source mismatch, unknown schema, 65,536/65,537-byte JSON | Exact body boundary enforced; invalid event has no dispatch/task and enters permitted durable review |
| A04 `test_transactions.py` | Fail between event/outbox and inbox/task writes | Whole transaction rolls back; no orphan durable work; repeat creates one scoped task |
| A05 `test_publish_confirm.py` | No route; missing one required binding; nack; timeout after acceptance | No false successful dispatch; missing subscriber detected; original IDs retained and one durable result per intended consumer |
| A06 `test_fanout_limits.py` | Two independent queues, one full; two workers on one queue | Other subscriber can receive before nack; retry completes missing work, no double business result; independent queues each get a copy |
| A07 `test_commit_ack_gap.py` | Kill consumer before commit, after commit/before ack; same event ID with altered body | Uncommitted work redelivers; committed work deduplicates; conflicting hash quarantined |
| A08 `test_consumer_stall.py` | Keep AMQP live; stall DB and saturate prefetch/semaphore | 10/30-second budgets trigger channel closure, not basic.cancel alone; uncertain commit deduplicates; other services progress |
| A09 `test_scope_permissions.py` | Wrong owner/domain/source; unauthorized bind/read/write/provision; forged SQL scope | Broker and non-admin DB role deny access; no cross-owner/domain task, source read or replay |
| A10 `test_lease_fencing.py` | Concurrent claims, kill worker, expire lease, old worker returns | At most two valid VM-wide slots; new fence wins; old completion/effects rejected; durable result applied once |
| A11 `test_schedule_recovery.py` | Concurrent scans, restart over due times, DST gap/fold and version change | One occurrence per key; atomic next_due/outbox; correct catch-up/coalesce/skip and audited limits |
| A12 `test_quarantine_retry.py` | Poison event, DB down, review queue down, repeated task error | Quarantine durable before ack; transport backoff does not exhaust business attempts; no tight requeue loop or lost review record |
| A13 `test_reconnect.py` | Broker restart preserving volume; DB/broker partitions during publish/commit | Reconnect gated on approved topology, unknown states stay pending, durable backlog eventually drains |
| A14 `test_broker_loss_replay.py` | Delete isolated broker volume after positive confirms | Recreate topology and recover incomplete consumers from retained events/dispatch, not just unpublished outbox; completed work not reapplied |
| A15 `test_retention_clock.py` | Just before/at/after 30/90 days; late terminal/attempt; unresolved work | Correct window/holds/dedup floor; events strictly older than 30 days held for review, daily cleanup dependency-safe |
| A16 `test_source_approval_replay.py` | Deleted/missing bytes, wrong owner, expired/revoked approval, restore older data | Replay blocked/quarantined; no deleted bytes resurrected or external action performed; RPO loss explicitly reported |
| A17 `test_pressure.py` | Separate count/byte queue bounds; memory/disk alarms; max connections/channels | reject-publish retains DB work; prefetch/in-flight/quotas bound memory; deadline survives blocked publishing; no host disk fill |
| A18 `test_lifecycle_health.py` | SIGTERM/process crash; dependency unavailable; private health probe | Graceful or lease-safe recovery; readiness degrades; logs redact bodies/secrets; no unauthenticated content endpoint |
| A19 `test_backup_restore.py` | Restore synthetic DB/source recovery set; fence old workers | Schema/checksums, scope and task/dispatch consistency verified; old external-effect ledger reconciled before release |
| A20 `test_capacity_profile.py` | Specified ingress/burst/replay under resource caps | Reproducible measurements meet the provisional workload gate below; no production capacity claim from unit tests |

Pressure fixtures must have their own disposable filesystem/container limits. Simulate disk alarms or use a bounded test volume; never fill the development host. Shortened test thresholds are allowed for deterministic scenarios, but assert that reviewed production defaults remain unchanged. Use pytest async support (locked pytest-asyncio or an equivalent explicit async runner), markers for real-service integration, and readiness polling with deadlines rather than arbitrary sleeps.

## Workload gate and evidence

Propose a reference synthetic workload for approval: two independent logical subscribers; 1 event/second sustained for 30 minutes; a 100-event/second burst for 10 seconds; event bodies up to 4 KiB normally and separate 64-KiB boundary cases; two handoff slots/process and two global synthetic worker slots. After a 1,000-event broker-loss fixture, cap replay at 10 events/second and require all intended durable handoffs within 180 seconds of dependencies/topology becoming ready. Do not bypass the replay rate to meet the target.

Provisional pass thresholds on the approved reference environment: no missing required consumer, no double-applied synthetic operation, p95 active handoff under 2 seconds at steady load, all 100 in-flight and prefetch bounds respected, no normal-load memory/disk alarm or OOM, broker RSS below the 2-GiB alarm, no sustained host swapping, and complete burst backlog drain within 180 seconds after input stops. Record body/header sizes, hardware, images/lock, database RTT, sample counts, p95/p99 handoff/confirm latency, RSS/CPU, disk latency, oldest dispatch age, replay speed and drain time.

Local/CI acceptance proves correctness under its recorded limits. Proving the proposed DigitalOcean/Supabase capacity requires a separately authorized reference environment; no environment is provisioned by this plan. If thresholds fail, change the proposal and cost estimate rather than declaring success. M7 additionally proves replacement-VM, independent alerts, MacBook-off continuity and approved RPO/RTO; M0 supplies tested synthetic backup/health hooks and operator runbooks first.

## Verification commands and completion evidence

Retain the existing gate after import, using its reviewed environment setup:

```console
uv lock --check
uv run ruff check .
uv run mypy
uv run pytest
```

The future CI change must provide a unit/legacy job and a separate required integration job selecting `tests/integration` with synthetic service fixtures. A full `uv run pytest` gate needs those fixtures; unavailable required services must fail rather than silently skip tests. A developer may run a unit subset for feedback, but it is not the complete gate. Collect test counts, logs and image/schema/lock hashes as artifacts. Strict mypy continues over `src/markos` and `tests`. Never let CI source production config or invoke provider CLIs.

For each M0 slice, attach changed paths, numbered A01–A20 evidence, source/data manifests and gate output to its PR. Mark an unexecuted or environment-dependent case pending; do not replace it with a prose claim. M0 closeout requires all synthetic foundation cases, backup/monitor hooks and reviewed remaining production-readiness gates. It does not mark M1–M8 complete.

## Decisions and scope limits

Before implementation: approve this import method, ownership/event schema, handoff deadlines, Psycopg/SQL approach, source-deletion contract and synthetic workload. Resolve exact role/vhost allocations, supported PostgreSQL major/connectivity, image digests/locked dependencies and aggregate connection budgets in the corresponding slice.

Before production: approve region/VM and Supabase plan, source-object provider, cost/operator/alert destination, source/audit/privacy/backup-erasure policy and measured capacity/recovery evidence. The outstanding Markdown knowledge-authority, vector/semantic retrieval and desktop-product decisions are not settled by operational M0 tables. Keep `KnowledgeDocument` and the legacy index behavior intact.

## Related

- [[ADR-003 RabbitMQ Internal Event Bus]]
- [[ADR-002 Cloud-first Deployment]]
- [[ADR-0002 GitHub as Canonical Project Record]]
- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 Roadmap]]
- [[MarkOS Cloud Deployment Architecture]]
