---
title: MarkOS v0.3 M0-C RabbitMQ Transport
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

# MarkOS v0.3 M0-C RabbitMQ Transport

## Result and prerequisite review

M0-C implements explicit RabbitMQ publishing/consumption adapters for the independent runtime. PostgreSQL remains authoritative for events, consumer intent, retries, receipts/tasks and operational review. No legacy application, PARA, Obsidian, DuckDB, personal notes or source-folder dependency is introduced. There is no business worker execution, Gmail/provider integration, automatic migration, production CLI/service loop or deployment.

PR #11 at `d0cfd67` was reviewed for transactional writes, authenticated identity/RLS, uniqueness, quarantine, claims/fences and checksummed migrations. No blocking correctness/architecture issue was found; its [hosted PostgreSQL CI passes](https://github.com/mschulz/markos-design/actions/runs/43147407279). M0-C preserves migration 0001 unchanged and adds migration 0002. This PR targets main and retains #11/#10 as prerequisites; review the M0-C range from `d0cfd67`, without merging into an old feature base. No merge is performed here.

## Verified transport baseline

The independent lock pins aio-pika 10.2.3, aiormq 7.2.5 and pamqp 5.0.0 on Python 3.13.14. `infrastructure/rabbitmq/profile.json` records RabbitMQ 4.4.0-management and its official multi-platform image digest `sha256:7ddb7fd4630026bc84f72760f33629e8c1ef3bb106360fb385fae0dd4ef886f3`, resolved from the [official tag metadata](https://hub.docker.com/v2/repositories/library/rabbitmq/tags/4.4.0-management). CI pulls that exact digest.

The [RabbitMQ download page](https://www.rabbitmq.com/docs/download) identifies 4.4.0 as latest on this review date; the [release table](https://www.rabbitmq.com/release-information) lists 4.4 community support through 31 July 2027. These dated checks and synthetic tests are not a production security/capacity audit. Recheck updates before launch.

Credential-free `definitions.example.json` defines durable topic exchanges, durable non-exclusive classic queues, exact bindings, 10,000 ready-message/32-MiB reject-publish bounds and a separate operator-review queue. CI provisions a fresh isolated vhost for every broker case from that file, caps queues/connections at 64/32 and starts the broker with channel_max 32 and max_message_size 65,536. The broader 4-vCPU/8-GiB host, memory/disk alarms, aggregate process budgets, load/cost and 30/90-day policy remain proposed and unmeasured; CI is not a production deployment.

## Connection and topology trust

`BrokerSession.connect(explicit_dsn)` uses robust connections, controlled heartbeat/connect deadlines, confirm-enabled publisher channels, mandatory returns and a separate dedicated consumer channel. URL credentials/host/vhost must be explicit. Remote AMQP/management endpoints require verified TLS; plain AMQP/HTTP is allowed only on loopback for synthetic tests. Caller URL query overrides and management redirects are rejected. The pinned client's transport factory exposes the owned StreamWriter for a hard socket abort if graceful channel/connection closure exceeds its budget. Public transport failures redact endpoint credentials and payloads.

Runtime adapters never declare or change exchanges/bindings. Consumer passive checks do not provision a queue; robust automatic re-consumption is disabled so one supervisor owns subscription recovery. An administrator provisions topology and separate service users before enabling publishers. Publishers can write only their domain event/review exchanges; each consumer can read only its queue and write its domain review exchange; runtime configure permissions are empty.

The topology auditor uses a separate management-tag identity scoped to the test vhost, with no AMQP configure/write/read permission. It performs bounded read-only HTTP GETs. The actual exchange, exact subscriber set/routing keys and each queue's durable/classic/non-expiring/reject-publish bounds must match before publication. Unexpected exchange policies/alternate exchanges fail closed. Missing one binding is detected even when another queue would satisfy mandatory routing. Administrative changes must pause/quiesce publishing: HTTP topology audit and AMQP publication are not one atomic operation. Retained intended-consumer state and M0-E reconciliation remain necessary for races or broker data loss after a positive confirm.

## Publication and durable retry

`Publisher.publish_once()` claims at most ten stored events and never more than its bounded in-flight budget. The claim transaction exits before HTTP/AMQP calls. It audits the immutable intended-consumer snapshot from PostgreSQL, publishes the original event ID/canonical body persistently, and accepts only an actual `Basic.Ack` confirmation. False/None, nacks, mandatory returns, deadline expiry and unknown connection outcomes do not advance publication state.

A failed attempt persists a scoped failure code, attempt count and randomized 1–300-second exponential retry availability; pending state remains in PostgreSQL. A later pass reuses original event/operation identity. Queue-full partial fan-out can already have reached other subscribers, whose inbox/task deduplication remains required. Connection/resource retries do not exhaust business-task attempts. Expired or configured-oversize events enter durable review and a held outbox state rather than an automatic retry loop.

The adapter accepts validated profile bounds; connection/confirm/close budgets, consumer prefetch/handoff/deadlines and publisher in-flight/message/queue limits are applied at their explicit API boundaries. Resource alarms/host enforcement and service lifecycle are later operational work. No standalone daemon polls indefinitely; publishing passes are explicitly invoked by a future lifecycle component.

## Consumption and acknowledgement

The consumer resolves its service/owner/domain from the authenticated PostgreSQL login. It checks the envelope, message ID/type/content type and broker-validated `user_id` against an explicitly reviewed source-service-to-broker-user map. Publishing identities must have no impersonator tag, and federation/device bridges require separately reviewed trust mapping. Body identity alone is not an authentication grant; stored event identity/hash and intended consumer are revalidated in PostgreSQL before a task receipt commits.

Prefetch is ten by default, with two active handoffs. The independent total-age watchdog starts before semaphore wait (30 seconds); the active-handoff watchdog starts after acquisition (10 seconds). Watchdogs signal channel recovery without waiting for a database coroutine to cooperate with cancellation. Timed-out database transactions discard their suspect connection before reuse. Every subscription/handler has a generation: a late handler cannot acknowledge an obsolete generation even if its durable commit completed.

After `store.handoff` commits, created/duplicate receipts may be manually acknowledged. Invalid/oversize/unauthorized, conflicting and expired deliveries are acknowledged only after their scoped review plus review-outbox entry commits. Unregistered/non-intended handoffs are distinguished from transient database errors. Transient/uncertain commit or ack failures leave the delivery unacknowledged and trigger dedicated-channel closure, not an immediate nack/requeue loop. Reconnect/re-subscription is serialized with bounded backoff; graceful closure has a two-second default budget followed by socket abort. If handler cancellation cannot drain within the close budget, readiness stays false, the connection is aborted and the consumer stops with an inspectable failure instead of starting overlapping handlers indefinitely.

Long AI work never runs while retaining a broker delivery. Broker acknowledgement means durable handoff/review, not business completion or exactly-once effects.

## Durable review references

Migration 0002 adds `delivery_reviews` and `review_outbox` with RLS and scoped function-only writes. A review stores bounded reason, body byte count and SHA-256, not original sensitive bytes. Identical poison redeliveries deduplicate. Versioned `ReviewReference` and its generated schema contain only review ID, owner/domain, digest and reason. Hash-only rejected bodies are diagnostic records, not a replay archive or evidence that source bytes can be recovered.

A separately connected, confirm-enabled `publish_reviews_once()` sends one leased review reference per pass through the domain review exchange/queue. Failed/unknown results remain pending with durable backoff; success is recorded only after positive confirmation and a current fence. Review-queue availability is not required to persist quarantine or acknowledge its durable handoff; review publication can recover later. The database record remains authoritative if the review broker data is lost. Full retained-event/review reconciliation after broker storage loss belongs to M0-E.

## Evidence

The required hosted suite includes 194 tests: 143 unit/contract/configuration cases, 40 real PostgreSQL cases and 11 real RabbitMQ+PostgreSQL cases. Local execution passes 183 tests with the 11 broker cases explicitly skipped because RabbitMQ is not installed locally. CI sets both require-integration flags, so missing either service fails rather than silently skipping.

Real broker cases cover independent subscribers; missing-member audit; zero-route race/mandatory return; queue-full partial fan-out nack; unknown confirmation fault injected after actual broker acceptance; crash after DB commit before ack/redelivery; poison review plus confirmed reference; cross-domain/read/configure permission denial and validated user-ID rejection; competing replicas; connection abort/reconnect with one eventual subscription; and stalled-handoff channel recovery. Management monitoring is eventually refreshed, so that assertion polls to a bounded deadline. Unit tests additionally cover Basic.Ack-only acceptance, raw/final size/identity validation, semaphore wait budgets, cancellation-resistant handoff watchdogs, hard-close fallback and unsafe queue profiles. PostgreSQL cases prove retry delay/fence persistence, review atomicity/idempotency/isolation, review rollback, permanent rejection and cancelled-connection discard.

The first real-broker run exposed an incorrect bindings endpoint; the adapter now uses the official exchange-source binding endpoint and tests it directly. The suite is checked on the PR before marking ready. Source code/lock for the archived application, exact snapshots, import manifest and migration 0001 remain unchanged. The archive's 145-test preservation suite remains separate from this acceptance evidence.

## Remaining work

The subsequent [[MarkOS v0.3 M0-D Execution and Scheduling]] supplies fixed synthetic no-op execution, global lease slots and persistent scheduling; real agent execution/approvals remain future work. The subsequent [[MarkOS v0.3 M0-E Replay and Retention]] supplies synthetic source observations/deletion, age gates, replay/retention and recovery of missing handoffs including previously confirmed work; real source-provider/backup erasure remains future work. M0-F owns the production lifecycle, TLS/credential provisioning, host pressure/quotas/monitoring, backup/restore and operational measurements. This slice does not prove broker volume-loss recovery, exactly-once effects, production availability, source permission checks or 30/90-day cleanup. No cloud configuration, real account/provider, personal-note migration, merge or deployment is performed.

## References

- [aio-pika API](https://docs.aio-pika.com/apidoc.html) and the locked client source for confirm outcomes, explicit robust consume flags and transport factory integration.
- [Publisher confirms/manual acknowledgements](https://www.rabbitmq.com/docs/confirms), [validated user-ID and impersonator behavior](https://www.rabbitmq.com/docs/validated-user-id).
- [Management HTTP API](https://www.rabbitmq.com/docs/http-api-reference), [management permissions](https://www.rabbitmq.com/docs/management).
- [[MarkOS v0.3 M0 Implementation Plan]]
- [[ADR-003 RabbitMQ Internal Event Bus]]
