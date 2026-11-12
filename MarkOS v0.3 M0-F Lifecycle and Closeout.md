---
title: MarkOS v0.3 M0-F Lifecycle and Closeout
type: implementation-record
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# MarkOS v0.3 M0-F Lifecycle and Closeout

## Scope and prerequisite review

This slice implements the synthetic lifecycle in [[MarkOS v0.3 M0 Implementation Plan]]. It builds only on the independent `services/runtime` project. RabbitMQ remains the internal bus; PostgreSQL owns state; source objects are represented by synthetic fixtures. It introduces no PARA, Obsidian, Markdown authority, legacy application dependency, real provider, MCP client or classifier. [[ADR-005 Governed MCP Access for Agents]] and the rules-first M3 routing decisions remain unchanged.

PR #16 was reviewed at `83c230fcaba84501e207b6ad9ca38ee88e9984d5`: no blocking finding within its synthetic replay/retention scope. Its runtime CI passed 293 cases plus the separate broker-volume-loss case, and documentation CI passed. Review covered intended-consumer reconciliation, one-use scoped permits, source checks/deletion, policy clock boundaries, holds, roles and additive migration 0004. Synthetic observations do not prove real provider access or erasure. Restore fencing is addressed here: numeric leases alone can collide after restoring an older database.

The review branch preserves PR #15's documentation reconciliation and all PR #16 commits. Integration baseline `539ed6204629f3fdfa410e6110fed38a44af2d4d` contains those prerequisites; compare from that baseline for M0-F-only changes. Both prerequisite PRs remain separate reviews; this slice merges neither into main.

## Runtime lifecycle

`serve-synthetic --confirm-synthetic --config <explicit JSON>` runs one role: publisher, consumer, worker, scheduler, recovery or review. The example manifest uses two independent logical consumers and nine processes. Configuration is strict, versioned and test-only. Database/broker targets are restricted to named private test services or loopback test endpoints; an expected login-bound owner/domain/service identity must match. Credentials resolve only from explicitly named environment variables after this command is invoked. Preflight remains connection-free, with no secret resolution, migrations, topology creation or task execution.

The worker executes only the existing fixed no-op, with an optional bounded synthetic delay for fault tests. Recovery cleanup is disabled in example services; enabling it is explicit, capability checked and daily bounded. No plugin/tool handler discovery is added. Migrations and broker bootstrap remain separate privileged test-controller actions, never worker startup behavior.

Each process provides private loopback `/live`, `/ready` and `/metrics` endpoints. Readiness requires successful identity/generation checks and a dependency pass. Publisher failures remain unready until successful publication; idle polling does not clear a failed confirm. Liveness uses monotonic progress with a 45-second watchdog that exits a stalled process. A Compose health failure alone does not restart a container. JSON telemetry emits fixed event codes and bounded counters without identifiers, payloads, URLs or secrets; third-party connection logs are suppressed. Heartbeats are scoped by authenticated identity, component, instance and database generation, capped at four fresh instances per component and expire for capacity after 45 seconds.

SIGTERM/SIGINT stop readiness first, allow a bounded in-flight pass, record draining where the database is available, close subscriptions/connections and exit. A hard shutdown deadline backs up graceful cancellation. Uncommitted work recovers through existing durable lease/retry mechanisms; no exactly-once transport or provider effect is claimed. `unless-stopped` restarts failed processes and previously running services after a Docker daemon restart. Intentionally stopped services need an explicit start.

## Restore-generation fencing

Additive migration `0005_lifecycle_fencing.sql` preserves 0001–0004 exactly. Migrator-owned state contains a random runtime generation. Claims return that generation together with numeric fences. Every lease-sensitive publication authorization/finalization, retry, task completion/renewal and review completion requires both; the old unfenced entry points lose runtime EXECUTE grants. Missing or old generation tokens fail closed, including deliberately recreated numeric fence collisions.

The private migrator-only `fence_restored_state()` rotates the generation under the same advisory lock used by epoch-sensitive calls, resets leased outbox/review work and running tasks, abandons running attempts, clears executor slots/heartbeats and invalidates replay permits. It records restore history. Running services capture the initial generation and exit if it changes rather than silently adopting restored authority. Deploying this API change in the future requires stopping/upgrading all clients together; the old client API is intentionally incompatible with the new migration.

This fences lease-sensitive writes to the restored database. It does not revoke database credentials, prevent every non-lease API call by an old client, fence an unreachable surviving old database or stop an external provider. Future production recovery must isolate previous hosts, revoke credentials and reconcile unknown external outcomes before allowing effects. M0 has no external effects.

## CI and evidence boundaries

The workflow has three required synthetic jobs: normal contracts/database/broker and standalone wheel checks; actual broker-volume loss with PostgreSQL retained; and independent container lifecycle/restore. Documentation is checked separately. The lifecycle job is restricted to a unique labelled project on an ephemeral GitHub-hosted runner with its local Docker socket. No cloud VM, real account, personal folder or production server is contacted. The image build context contains only the independent project, uses an unprivileged user/read-only filesystem, and excludes the root archive and test dependencies.

The disposable harness uses server-authenticated TLS with a generated one-day CA for AMQP and management HTTPS, distinct least-privilege publisher/consumer identities and a read-only topology observer. This is password authentication over TLS, not mutual TLS. Each app is capped at 128 MiB, 0.25 CPU, 64 PIDs and 1,024 file descriptors; logs rotate at two 5-MiB files, shutdown grace is 15 seconds and no app health port is published. Test PostgreSQL/broker caps are 512 MiB each, with broker memory/disk alarms set to 256 MiB. These intentionally small test caps do not replace ADR-003's proposed production resource profile.

| M0 case | Evidence and remaining limit |
|---|---|
| A01–A12 | Prior strict contracts, atomic persistence, roles, broker handoff, execution and scheduling, plus independent wheel/container installs |
| A13 | Database outage and no external effects; real approval expiry/revocation remains future work |
| A14–A16 | Retained-event broker-loss replay, policy boundaries and synthetic source/deletion gates; real source/backup erasure remains open |
| A17 | Hard worker exit, SIGTERM, database/broker outage and Docker daemon restart with durable results; actual VM reboot/host boot remains unproven |
| A18 | Actual container caps, forced broker memory/disk alarms, pending outbox preservation and backlog drain timings; production load/capacity and remote alerts remain unproven |
| A19 | Quiesced PostgreSQL dump to fresh database, checksum manifest, deleted-object filtering, generation fencing and loopback notification fallback tests; real object provider, encrypted off-host backups and independent notification placement remain open |
| A20 | Independent wheel/image with no legacy/pytest imports and synthetic schedule → event → broker → two durable results; no real agents/providers |

`config/runtime/m0-closeout.json` records the acceptance disposition and production gates without asserting production readiness. The acceptance set contains 323 runtime cases: 198 unit/boundary, 110 PostgreSQL, 13 broker/database, one broker-volume-loss drill and one lifecycle/restore drill. Local checks pass 308 and explicitly skip the 13 broker cases; the two destructive harness drills run only in their dedicated hosted jobs. The archived application’s 145 checks and seven documentation-checker tests pass independently. CI prints bounded lifecycle timing/memory samples; they measure only its fixtures on that hosted runner. See [[MarkOS v0.3 M0 Runbook]] for the controlled test procedure and future recovery requirements.

## Closeout decision

The M0-A–M0-F synthetic foundation is prepared for review. Passing these checks is not deployment approval or completion of all production acceptance. The remaining decisions are provider/region/cost and network/secrets/TLS operations, real source permission/retention/deletion, backup/restore ownership and approved RPO/RTO, measured VM capacity/boot behavior, independently placed monitoring, real action approval/reconciliation and MCP-01–MCP-10 before enabling MCP. Native integrations remain independent of MCP. No merge or deployment is performed.
