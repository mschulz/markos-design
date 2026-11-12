---
title: MarkOS Cloud Deployment Architecture
type: architecture
status: draft
version: 1.6.1
owner: Mark Schulz
created: 2026-11-11
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - deployment
  - operations
---

# MarkOS Cloud Deployment Architecture

## Decision

Apply accepted [[ADR-002 Cloud-first Deployment]]: a DigitalOcean VM runs the always-on Docker Compose services; Supabase PostgreSQL holds durable state. The placement is accepted. The operational details and targets below are implementation requirements/proposals for review, not evidence of deployed infrastructure.

[[ADR-003 RabbitMQ Internal Event Bus]] selects RabbitMQ over AMQP, replacing provisional MQTT. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] replaces the old PARA/Markdown/DuckDB product baseline. Exact broker profile and operational targets remain proposals. Cloud placement is unchanged; the new runtime is independent of imported legacy code.

## Motivation

Jobs must continue without a MacBook, logged-in operator, local tunnel, local OAuth callback server or mounted personal filesystem. Production credentials and configuration must be available securely in the cloud.

## Service placement and network boundaries

| Location | Components and responsibilities |
|---|---|
| DigitalOcean VM / Compose | RabbitMQ internal broker; Gmail ingestion and watch renewal; outbox publisher/reconciler; bounded agent workers; persistent scheduler; notification gateway; authenticated web API/control plane and HTTPS entry point |
| Supabase PostgreSQL | Operational events, outbox/inbox deduplication, tasks, schedules and run history, leases, Gmail history cursor, agent/configuration metadata, approvals, conversations, notification attempts, audit and approved knowledge schemas |
| Cloud source-object storage (provider open) | Immutable email/attachment originals, checksums and access policy; database stores references. No MacBook-only originals |
| MacBook / iPhone | Authenticated browser/CLI clients; MacBook development and administration. Local development uses isolated synthetic data and credentials |
| External providers | Gmail / Google Cloud Pub/Sub mailbox-change notifications, model APIs and selected push adapter. Google Pub/Sub is distinct from the MarkOS RabbitMQ broker |

Clients reach the HTTPS API; the API and workers use TLS to Supabase and provider APIs. Compose service traffic stays on a private network; restrict AMQP and broker management endpoints to authorized private/admin access, and do not expose unauthenticated broker or database ports to the public internet. Broker identities use TLS, per-service credentials and least-privilege exchange/queue permissions; workers cannot bind arbitrary domains or provision unrestricted queues. Application checks still enforce owner/domain access. Administrative access is restricted and authenticated. Validate Pub/Sub push identity at a cloud HTTPS endpoint if push is selected; a cloud pull subscriber is an alternative implementation choice. Neither path depends on a personal-device tunnel.

Keep secrets outside Git and images, with least-privilege runtime roles and protected cloud-side injection. Store recoverable credential material encrypted, with recovery access independent of the VM and MacBook. Rotate credentials after suspected compromise. Supabase administrative credentials never reach browser clients. Select a production plan/capacity that avoids intentional inactivity suspension; verify connectivity, connection limits and backup entitlements before launch.

## Knowledge and Markdown storage

PostgreSQL holds structured facts, entities, relationships and provenance under owner/domain scope. Immutable originals live in protected cloud object storage with database references and checksums. Markdown summaries/exports are derived artifacts; an optional standard folder has no PARA hierarchy or Obsidian requirement. No production service depends on a mounted personal note directory, legacy DuckDB index or old filesystem watcher. Source/export access, deletion, retention and recovery contracts require explicit implementation.

## M0-D implementation evidence

[[MarkOS v0.3 M0-D Execution and Scheduling]] implements fixed synthetic no-op execution and versioned interval/daily scheduling for review, with database leases/fences/global slots, atomic occurrences/events/outbox and audited missed policies. The broader requirements below remain targets for real agents and deployed operations. CLI/service lifecycle, production job types, approvals, source access and full replay/recovery are not delivered by this slice.

## Persistent scheduling and missed-job recovery

The scheduler runs in the VM, while PostgreSQL owns scheduling truth. Persist a stable schedule ID/version, owner/domain, job type, payload reference, enabled flag, interval/cron expression, IANA timezone, next due time, missed-run policy, catch-up limit, retry/backoff limit and execution timeout. Store UTC instants and define daylight-saving behavior explicitly for wall-clock schedules.

Each occurrence has a durable run record: schedule/version, original due time, unique occurrence key, status, attempts, availability time, lease owner/expiry, result or error and timestamps. A transaction locks due schedule rows, inserts uniquely keyed occurrences and their task/outbox records, and advances next-due state together. Concurrent scheduler processes must not create duplicate occurrences. Use database time for claiming and leases; workers renew leases and only the current valid lease/fencing token may finalize a run.

On startup and periodically, reconcile overdue schedules, expired leases, unfinished runs and unpublished outbox entries. Choose and persist per-job policy before enabling a schedule:

- **Catch up:** replay each missed occurrence within a bounded count/age window, in due-time order when ordering matters.
- **Coalesce:** create one current run representing the missed interval, suitable for watch renewal and latest-state maintenance.
- **Skip:** record missed occurrences as skipped with reason and audit; never silently drop overdue jobs.

Record catch-up truncation and alert an operator; do not create an unlimited recovery storm. Weekly consolidation, reconciliation, watch renewal and backup jobs each need explicit policies. Validate current Gmail watch expiry/cursor after downtime; use history reconciliation/full resync when necessary. An old watch-renewal occurrence must not schedule from a stale expiry.

Execution is at-least-once. Deduplicate schedule occurrences and business operations, preserve idempotency keys through retries and enforce approval expiry at execution time. External delivery can have an ambiguous result after a crash: reconcile provider status where available or surface uncertainty for review; do not promise exactly-once notifications or repeat consequential actions blindly. Poison jobs enter dead-letter/operator review after bounded retries.

If PostgreSQL is unavailable, stop claiming jobs and performing unrecorded side effects. Retry connections with backoff/jitter and report degradation. Acknowledge external ingress only after its recovery state is durable; reconcile Gmail changes after reconnection.

## Container and host lifecycle

[[MarkOS v0.3 M0 Implementation Plan]] defines an independent runtime project, synthetic foundation slices and tests. Do not build on the old markos package or inherit its toolchain automatically. Classic-queue consumers need a 10-second active handoff and 30-second total unacknowledged-age watchdog, dedicated consumer channels, and channel-close recovery after uncertain commits; health checks and heartbeats alone do not detect a stalled database handoff. Keep publishers separate and resolve retries by owner-scoped inbox identity.

Set `restart: unless-stopped` for every long-running Compose service, including scheduler and ingestion. Enable Docker at host boot and verify recovery after a VM reboot. An intentionally stopped container remains stopped with this policy; maintenance recovery must explicitly start it. One-shot migrations and restore tasks need deliberate invocation and failure handling rather than an unlimited restart loop.

Define readiness/liveness checks, startup grace periods, resource bounds and graceful termination. Workers finish safely or release/expire leases on shutdown. Persist RabbitMQ data in a named volume; use durable exchanges/queues and persistent messages, with confirms and manual acknowledgements as specified in [[ADR-003 RabbitMQ Internal Event Bus]]. Keep reviewed topology definitions, policies and permissions recoverable separately from secrets. Propose RabbitMQ 4.4.0 with durable classic queues and aio-pika 10.2.3. The ADR-003 profile proposes a 4-vCPU/8-GiB/80-GiB SSD VM planning envelope, a 4-GiB broker cap with 2-GiB absolute memory alarm and a 10-GiB free-disk alarm (warning at 20 GiB). The broker CPU ceiling is 4, not reserved cores; shared-host contention and all service budgets require M0 measurement and cost review. Enforce ADR-003 queue, message, connection, channel, prefetch and publisher limits; reject-publish backpressure leaves work in PostgreSQL. M0-C pins the tested image digest and independent client lock; see [[MarkOS v0.3 M0-C RabbitMQ Transport]]. Production capacity and rollout remain unverified. Quorum replication requires a separately reviewed multi-node failure-domain design to provide availability. A single node on this VM is not highly available. Never store authoritative tasks only in container files.

Use healthy dependency conditions for local Compose startup where appropriate; services still need runtime reconnect/backoff for broker and remote Supabase failures. A health check alone does not restart an unhealthy process: exit on unrecoverable failure or alert for operator remediation. Rotate logs, monitor disk and prevent crash loops from exhausting the VM.

Pin reviewed image versions/digests, record the deployed Git commit, review migrations before rollout and retain the last working images/configuration. Application rollback is permitted only against a compatible schema; take a verified backup before a destructive migration and define a separate data-restore plan.

## Replay retention and cleanup

Use the proposed ADR-003 policy: 30-day routine event replay, retention through 30 days after required work is terminal, a 90-day deduplication floor after the latest terminal transition/attempt, and no age-based purge of unresolved work or required source bytes. Events older than 30 days require operator review before delivery/replay. Source deletion blocks replay. Audit dependency-aware daily cleanup and monitor database/object growth; a broker queue limit does not cap PostgreSQL backlog. These are proposed operational-event rules, not approval of the final source/audit/privacy retention policy. They do not extend the backup RPO below.

## Backups and recovery objectives

Proposed initial targets for owner approval before launch: **RPO at most 24 hours** for durable state/source objects, **RTO at most 4 hours** after an operator begins recovery. These are targets to validate in drills, not an availability guarantee. Any tighter requirement must change backup frequency/capability and budget.

Require daily encrypted off-VM database backups or verified managed backups with equivalent coverage, plus an independently recoverable export. Verify the selected Supabase plan's retention and restore procedure; do not assume point-in-time recovery is included. Proposed retention is 7 daily and 4 weekly independent copies, subject to privacy/deletion review. Alert on failed or stale backups and periodically verify checksums and restore access.

Back up source-object bytes/versions separately from database metadata, plus broker persistence, reviewed configuration, deployment manifest and encrypted secrets recovery material. Database backups alone do not restore attachment bytes, including when Supabase Storage is eventually selected. VM snapshots supplement these backups; they do not replace a consistent database/object recovery set. Record backup IDs, timestamps, source checksums and schema/image versions together.

Rehearse restore in an isolated environment before launch and periodically thereafter. Check domain permissions, source references/checksums, schedule/run state, outbox/inbox consistency and notification audit. Restoring an older database can forget operations already sent to providers; fence the former scheduler/workers and reconcile external delivery before releasing side effects.

## Monitoring and response

Collect structured logs with trace/correlation IDs and redacted sensitive content. Track API readiness, scheduler heartbeat/last scan, overdue-job age, failed/expired leases, dead letters, outbox age, worker progress, RabbitMQ ready/unacknowledged depth and oldest pending-work age, confirm failures/returns, broker disk/memory alarms, Gmail cursor lag/watch expiry, notification failures, Supabase connectivity/latency/connection usage, VM CPU/memory/disk and backup freshness.

Use an external uptime/heartbeat monitor outside this VM and a direct alert route independent of the MarkOS notification gateway. Otherwise a failed VM cannot report its own failure. Before launch, assign an operator, alert destination, thresholds and escalation path. Proposed starting thresholds: missing scheduler heartbeat for 5 minutes, overdue critical job beyond its job-specific grace, and backup age above 26 hours. Tune during acceptance drills; Gmail watch-renewal alerts must allow time to act before expiry.

## Failure and recovery procedures

| Failure | Response and completion evidence |
|---|---|
| Worker/container exit | Restart policy recovers process; reconcile expired lease and idempotency state; confirm run completes once in business state |
| Hung/unhealthy service | External health/heartbeat alert; inspect logs and dependency health, restart deliberately; prove backlog drains |
| VM reboot | Docker boots, non-stopped services recover, broker volume survives, scheduler catches up; no operator login required |
| Lost VM/disk | Fence old instance to prevent two writers; prepare replacement only under deployment authorization, restore reviewed images/config/secrets/broker data, connect Supabase, verify health before releasing jobs |
| Supabase/network outage | Degrade readiness, stop unsafe claims/acks/side effects, back off; reconcile ingress, leases and outbox after connection returns |
| Database corruption/restore | Pause writers and delivery, restore verified database/object recovery set into isolation, validate consistency, reconcile side effects, then re-enable with bounded catch-up |
| Gmail/push provider outage | Persist retry state, back off within limits, alert on expiry/backlog; reconcile before duplicate sends |
| Credential compromise | Disable affected access, rotate secrets, audit events, restore trusted configuration before resuming |

A normal VM replacement reconnects to intact Supabase state; it must not restore an older database unnecessarily. A restored RabbitMQ broker may redeliver; lost broker data can also remove previously confirmed messages. Rebuild missing work from retained source events, intended-consumer dispatch records, outbox/inbox and run state, not only unpublished outbox entries. Preserve original event/operation keys, verify topology before replay and suppress external side effects until reconciliation finishes. Do not delete the only replay source merely because publishing was confirmed. Keep a recovery checklist and access path usable from another authorized computer.

## Production acceptance evidence

These are future milestone tests, not completed checks in this documentation PR:

1. Shut down the MacBook; synthetic Gmail ingestion, scheduled work, agent processing, API and notification delivery continue.
2. Kill a worker and reboot the VM; service recovery and lease retry preserve durable results.
3. Pause the scheduler across multiple due times; verify catch-up, coalesce, skip, limits and concurrent claims.
4. Disconnect RabbitMQ and Supabase independently; verify confirm/return handling, durable handoff, bounded retries/quarantine and no unrecorded side effects. Delete isolated test broker storage and recover previously confirmed unfinished work from database history; verify independent subscriber delivery and crash-before-ack deduplication.
5. Restore database and attachment backups into isolation and replace the VM; measure RPO/RTO, validate provenance and prevent duplicate provider actions.
6. Disable the VM/notification gateway; verify the independent monitor alerts the operator.

## Consequences

### Benefits

The cloud can process jobs while clients are off. Durable scheduling, bounded replay and independently verified recovery make failure behavior reviewable.

### Costs

Single-VM downtime remains unavoidable without a later availability design. Operators need budget, independent alert delivery, backup access and rehearsed recovery. Detailed contracts and providers remain open; structured knowledge authority and replacement of the legacy design are settled by ADR-004.

## Related

- [[ADR-003 RabbitMQ Internal Event Bus]]

- [[ADR-002 Cloud-first Deployment]]
- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 Roadmap]]
- [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
- [Compose startup/readiness](https://docs.docker.com/compose/how-tos/startup-order/)
- [Supabase database backups and storage boundaries](https://supabase.com/docs/guides/platform/backups)

Provider behavior was checked against these official references on 11 November 2026. Proposed MarkOS policies and targets are design choices, not provider guarantees.

## M0-E synthetic recovery evidence

[[MarkOS v0.3 M0-E Replay and Retention]] proves retained intended-consumer recovery after removal/recreation of an owned disposable RabbitMQ volume while PostgreSQL stays intact. Its scoped synthetic source gates, tombstones and staged cleanup do not implement cloud object/backup erasure or restore tombstone reapplication. Production lifecycle, real-provider recovery, restore/pressure and operational drills remain future acceptance.


## M0-F synthetic lifecycle evidence

[[MarkOS v0.3 M0-F Lifecycle and Closeout]] adds explicitly confirmed synthetic processes, private readiness/liveness/heartbeats, bounded shutdown, generation-fenced lease operations and a restricted container acceptance harness. The CI drill covers process/dependency faults, forced broker memory/disk alarms, Docker daemon restart and fresh-database/fixture-object restore. It does not validate a VM reboot, production capacity, cloud backup/erasure or independent alert placement. [[MarkOS v0.3 M0 Runbook]] and the closeout ledger keep those production gates explicit; no cloud resource is provisioned or deployed.
