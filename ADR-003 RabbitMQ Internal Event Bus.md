---
title: ADR-003 RabbitMQ Internal Event Bus
type: architecture-decision
status: accepted
version: 1.3
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - messaging
  - reliability
---

# ADR-003 RabbitMQ Internal Event Bus

## Decision

RabbitMQ over AMQP 0-9-1 is the selected internal event transport, replacing provisional Mosquitto/MQTT. The owner's subsequent direction and architecture reset are recorded in [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]]. Selection is accepted; exact versions, queue/resource/flow limits and replay-retention values below remain proposed and require revalidation and measured acceptance. No deployment is performed by this record.

Preserve accepted [[ADR-002 Cloud-first Deployment]]: a DigitalOcean VM runs Docker Compose services and Supabase PostgreSQL holds durable operational state. The MQTT references in that earlier ADR describe the previous provisional transport, not a requirement to retain it. This ADR scopes transport; ADR-004 separately supersedes the legacy product/storage design; managed RabbitMQ or a multi-node cluster would require a separate placement/cost review.

MQTT may later serve device/Home Assistant adapters with explicit topic-to-event mapping. Google Cloud Pub/Sub still supplies Gmail mailbox-change notifications to the ingestion bridge; it is a separate ingress service.

## Motivation

MarkOS needs independently buffered subscribers, competing task workers, explicit durable handoff and inspectable failures. RabbitMQ's queue/exchange model fits this proposed workflow more directly than treating MQTT delivery acknowledgement as business completion. PostgreSQL remains the durable authority for events, tasks and recovery; changing brokers does not remove the transactional outbox or deduplication.

## Routing and domain boundaries

Propose a topic exchange per domain, `markos.{domain}.events`, with `v1.{category}.{event}` routing keys, such as `v1.email.ingested`. Declare durable queues and bindings for each logical subscribing service/domain before publishing. Electricity processing and audit receive separate queues; replicas of one service compete on its queue.

Freeze names, compatible event schemas, intended-consumer dispatch records and provisioning permissions in M0. Isolate environments and domains with reviewed virtual-host/resource permissions; grant publishers and consumers only their required exchange/queue access. Workers cannot create arbitrary bindings across domains. Validate owner/domain in application code as well. Transport naming alone does not enforce isolation. Require UUID owner_id in every event envelope, compare it with trusted service scope and source access, and include owner/domain in inbox/task/business-operation keys. Keep sensitive bodies in protected source storage and publish bounded metadata/references.

## Publication and durable handoff

A PostgreSQL transaction commits domain changes with an outbox event. Publish persistent messages using publisher confirms and mandatory routing. A negative confirmation, returned unroutable message or unknown connection outcome keeps the entry pending; retry with its original event ID. Record dispatch only after positive confirmation and no return. Mandatory publishing detects zero matching queues; separately audit that every required subscriber binding exists.

A consumer commits inbox identity `(logical consumer, owner_id, domain, event_id)` and a durable task or short business result before manual acknowledgement. A crash after commit but before acknowledgement causes harmless redelivery through deduplication. Long-running agent work is claimed from PostgreSQL with leases/fencing; task completion is separate from broker handoff. Separate business operation keys prevent duplicate effects. Ordering across workers is not guaranteed; define entity sequencing where needed.

## Retry and recovery

Use the persistent database scheduler for bounded exponential backoff/jitter and attempt limits; never build an immediate nack/requeue loop. Before acknowledging invalid or exhausted work, persist bounded diagnostics and a source reference in an access-controlled quarantine. A dead-letter/review queue aids operations, but its delivery is not the only record. Validate the selected version/queue type's dead-letter safety rather than assuming lossless forwarding.

Retain event payload references, intended-consumer dispatch and processing state long enough to meet the approved recovery policy. Broker storage loss can remove positively confirmed work. Reconciliation must identify missing per-consumer handoffs and republish even previously confirmed events, using original IDs and business keys. Archived originals and retention limits must support that recovery; define the supported replay window before production. Reconcile approvals and external effects before resuming. Replay suppresses side effects by default.

These are at-least-once workflows. Neither publisher confirms nor consumer acknowledgements promise exactly-once business actions.

## Hosting and operational limits

Use a persisted broker volume, recoverable topology/policies, TLS, restricted management access and per-service credentials. The proposed version, queue, resource and retention choices below are the M0 starting profile. Keep scheduler, outbox and recovery monitors independent of consumer progress.

The accepted single-VM topology has downtime on VM failure. Propose durable classic queues for this node; quorum queues remain a future replicated option requiring a separately reviewed multi-node topology. Multiple containers on one VM do not remove that VM's failure risk. Resource sizing below is a review proposal, not spending or provisioning authorization.

## Proposed single-VM choices

These are MarkOS engineering starting values for low-volume mail/agent workflows, not vendor defaults or measured capacity. Keep status proposed until review and M0 acceptance. Freeze the resolved image digest and Python dependency lock in the future implementation PR; this documentation does not add runnable configuration.

| Choice | Proposed value | Reason and qualification |
|---|---|---|
| Broker | RabbitMQ **4.4.0**, official `rabbitmq:4.4.0-management` image, pinned by digest before use | Current stable release checked 12 November 2026; 4.4 community support is listed through 31 July 2027. Recheck supported patches/security notices at implementation and monthly; update through tested PRs, never floating `latest` |
| Python client | **aio-pika 10.2.3**, exact lock including transitive dependencies | Async API, robust reconnection, confirms and mandatory returns suit async ingestion/API services. Requires Python 3.11+ according to the earlier review; choose and test the independent runtime interpreter/library set in M0-A/M0-C rather than inherit the archived project |
| Queue type | Explicit `x-queue-type=classic`, durable, non-exclusive, no auto-delete; persistent messages; durable exchanges | Classic storage v2 is built into RabbitMQ 4.x. This node has no replica to benefit from quorum availability; retain database replay. Changing type later requires a reviewed drain/redeclare migration |
| VM planning envelope | **4 vCPU, 8 GiB RAM, at least 80 GiB usable SSD-backed storage** | Keep Supabase and source objects remote; no local model inference or heavy bulk extraction. This is shared compute, not four dedicated broker cores |
| Broker container | At most **4 CPUs**, **4 GiB hard memory cap**; absolute memory alarm **2 GiB** | Cap is a ceiling, not a reservation. Budget the other 4 GiB for OS/cache and bounded MarkOS services; load-test contention. Memory alarm applies backpressure before the hard cap; it is not an OOM guarantee |
| Disk safety | `disk_free_limit.absolute` equivalent to **10 GiB**; independent warning below **20 GiB** on the broker filesystem | Monitor actual named-volume backing filesystem, host logs/images and database backlog; rotate logs. Disk alarms block publishing, not safely discard pending work |
| Message body | **64 KiB** maximum (65,536 bytes), enforced before publish and by broker `max_message_size` | Metadata/references only. Oversize events enter durable review instead of repeated channel-failure retries; bodies/attachments stay in source storage |
| Per-queue ready backlog | **10,000 messages or 32 MiB**, whichever is reached first; `overflow=reject-publish` | Use policies, including review queues. Do not accept default drop-head. Ready limits exclude unacknowledged messages and metadata; prefetch and memory limits also matter |
| Topology and connection budget | At most **64 queues total**, **32 AMQP connections total**, **32 channels per connection**; file-descriptor limit **65,536** | Enforce reviewed provisioning quotas whose per-vhost/user allocations sum to the total; alert on leaks. Reuse long-lived connections, normally separate publisher and consumer connections per service |
| Consumer flow | **Prefetch 10 per consumer**, at most **2 concurrent database handoffs per process**; **10-second active handoff / 30-second total delivery-to-ack age** initially | Total age includes semaphore wait. On timeout close the affected consumer channel; do not ack uncertain commits. Never hold deliveries during LLM work. Limit expensive database-task execution separately to **2 concurrent agent runs across the VM** |
| Publisher flow | At most **100 in-flight confirms per publisher process**, **10-second confirm deadline**, **30-second negotiated heartbeat**, **10-second connection deadline** | Timeout is an unknown outcome, not rejection. Persist pending state and retry original IDs using jittered backoff from **1 second to a 5-minute ceiling**; resource-blocked transport does not exhaust business-task attempts |
| Replay rate | At most **10 events/second and 100 events per database batch** initially | Share publisher in-flight limits with recovery traffic; pause on alarms and allow live ingress capacity. Tune only from M0/M7 evidence |

RabbitMQ's production guidance recommends four cores, 4 GiB RAM and avoiding colocated I/O-heavy services. The shared VM is a deliberate low-volume compromise within the accepted topology. Before launch, measure broker RSS, host pressure, disk latency and backlog drain with simultaneous workers, realistic bursts and outage catch-up. If it fails, propose a larger/dedicated node or revised placement with cost review; do not assume this profile qualifies production automatically.

For aio-pika, use `connect_robust`, a confirm-enabled publishing channel with `on_return_raises=True`, mandatory publication, explicit persistent delivery and manual ack after commit. Handle returned messages, nacks, disconnects and timeout/cancellation without advancing outbox state. Reconnection restores connectivity/topology, not business idempotency or proof that an uncertain publish failed. Keep robust recovery declarations within service permissions and test topology recovery before enabling publishers.

RabbitMQ 4.3+ supports delivery acknowledgement timeouts only for quorum queues. For classic queues, use one consumer per dedicated channel and an independent application watchdog: 10 seconds for active database handoff, 30 seconds total unacknowledged age including semaphore wait. Propose transaction-local statement_timeout 5 seconds and lock_timeout 1 second. On stall or uncertain commit, do not ack; close the channel with a 2-second close budget and abort the consumer connection if closure hangs. Cancellation alone does not requeue deliveries. Gate reconnect/resubscription, resolve uncertain commits from the owner-scoped inbox, and test saturated prefetch plus sibling redelivery. Keep the publisher's independent confirm deadline; heartbeat is not a handoff deadline. See [[MarkOS v0.3 M0 Implementation Plan]].

When a full queue nacks a routed event, other subscriber queues may already have accepted it. Retry original IDs and deduplicate per consumer; never erase the source or skip the blocked subscriber. Bound publisher memory and leave backlog in PostgreSQL while RabbitMQ is blocked. Alert at 80% of a queue limit for 5 minutes, oldest unhanded-off dispatch age above 15 minutes (critical at 60 minutes, or sooner for job-specific deadlines), any memory/disk alarm, and broker RSS above 1.5 GiB for 5 minutes. Independently monitor retained database/object growth; a bounded broker does not bound the outbox.

Classic dead-letter forwarding is not a lossless handoff. Persist quarantine and its source reference in PostgreSQL before acknowledging; send a confirmed review reference through its own outbox. Broker DLX notifications are secondary. Keep no automatic queue/message TTL or queue-expiry policy on work/review queues; evaluate business expiry and human approval expiry explicitly in application code.

## Proposed replay retention

- **30-day routine replay window:** retain replayable event envelopes, payload/source references and intended-consumer dispatch state in PostgreSQL for at least 30 days from event creation and at least 30 days after all required tasks/consumers are terminal, whichever is later. Keep referenced bytes accessible under the same domain/owner permissions for that period.
- **Unfinished work is held:** never age-delete unpublished events, incomplete dispatches/tasks, unresolved quarantine or their required source bytes. Alert on age/capacity; pause affected ingress and escalate before storage exhaustion rather than silently purge. An operator resolves or explicitly cancels work with audit.
- **90-day deduplication floor:** retain minimal consumer inbox identities, operation keys and terminal outcome records until at least 90 days after the latest terminal transition or attempt. Retain them longer while referenced work is unresolved. This is a replay safety floor, not a final financial/audit/legal retention policy.
- **Age gate:** ordinary delivery/replay of events more than 30 days old enters durable operator review; it never blindly executes external effects. Routine recovery preserves original event/operation IDs and rechecks current source access, deletion state and approvals. Older unresolved work can be resumed only after scoped review; older archived work requires explicit reprocessing review and cannot assume expired dedup records still exist.
- **Cleanup and deletion:** run audited daily cleanup with dependency checks. A privacy deletion request is an explicit workflow: redact/delete the payload, record a minimal permitted tombstone and block replay, rather than letting retention override deletion. Do not resurrect deleted bytes from an archive/backup. Final source/audit retention and backup-erasure policy still require review before real data.
- **Recovery limit:** 30-day replay retention is not 30-day zero-loss backup. The separately proposed 24-hour RPO still bounds possible database/object loss after restore; reconciliation cannot recreate events absent from the recovery set. M7 must prove replay from that set and reconcile external effects before release.

M0 must test the 30/90-day boundaries with a controlled clock, pending-work holds, deletion tombstones, payload availability, queue-full partial fan-out, blocked-publisher backpressure and memory/disk pressure using synthetic fixtures. No real queue, data-retention policy or resource limit is applied by this PR.

## Alternatives considered

- Retain Mosquitto/MQTT internally: feasible with durable application machinery, but less direct for independent service queues and explicit work handoff; retain MQTT as an optional device adapter.
- Use only PostgreSQL polling: fewer dependencies and viable at small scale; not selected for this proposal because the planned architecture calls for routed asynchronous event distribution.
- Use RabbitMQ as the event archive or business database: rejected; retained PostgreSQL events, dispatch and task records remain required for recovery.
- Add RabbitMQ Streams, a managed broker or a cluster immediately: deferred pending replay/availability requirements, topology and cost review.

## Acceptance before foundation completion and launch

Approve the M0 contract, then implement synthetic tests for confirm/return handling, unknown publish outcomes, independent subscribers and competing workers, domain/owner denial, database commit before ack, bounded poison review, restart persistence and complete broker-storage-loss replay. Prove no lost durable work or double-applied business effects within the approved retention window. Include side-effect suppression and expired-approval tests. M7 rehearses recovery and operational targets. None of these runtime tests has been executed in this documentation change.

## Consequences

### Benefits

Logical subscribers have explicit backlogs and failure boundaries. Additional workers share service work without changing producers. Broker delivery and durable task lifecycle become separately observable.

### Costs

RabbitMQ adds memory/disk planning, permissions, topology management, upgrades and operational monitoring. Database reconciliation and idempotency are still necessary. Single-VM availability and cross-system transaction gaps remain.

## Related

- [[MarkOS v0.3 M0 Implementation Plan]]

- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 Roadmap]]
- [[MarkOS Cloud Deployment Architecture]]
- [[ADR-002 Cloud-first Deployment]]
- [[ADR-0002 GitHub as Canonical Project Record]]

## References

- [Consumer acknowledgement timeouts and cancellation](https://www.rabbitmq.com/docs/consumers#acknowledgement-timeout): classic-queue application watchdog and channel-close recovery rationale.

- [RabbitMQ current release](https://www.rabbitmq.com/docs/download), [support lifecycle](https://www.rabbitmq.com/release-information) and [official image tags](https://hub.docker.com/_/rabbitmq).
- [aio-pika 10.2.3](https://pypi.org/project/aio-pika/10.2.3/) and [client API](https://docs.aio-pika.com/apidoc.html).
- [Classic queue storage](https://www.rabbitmq.com/docs/classic-queues), [queue limits and overflow](https://www.rabbitmq.com/docs/maxlength), [memory threshold](https://www.rabbitmq.com/docs/memory), [disk alarms](https://www.rabbitmq.com/docs/disk-alarms), [configuration](https://www.rabbitmq.com/docs/configure) and [production sizing guidance](https://www.rabbitmq.com/docs/production-checklist).

The prior profile review recorded these references on 12 November 2026. This architecture correction does not recheck vendor release/support claims. Revalidate versions, security, image digest and client compatibility before implementation; the numbers remain proposals. Internal RabbitMQ selection is accepted.

- [RabbitMQ exchanges](https://www.rabbitmq.com/docs/exchanges): exchange-to-queue routing.
- [Publisher confirms and consumer acknowledgements](https://www.rabbitmq.com/docs/confirms): separate publisher/broker and consumer/broker confirmations.
- [Dead-letter exchanges](https://www.rabbitmq.com/docs/dlx): failure routing and safety considerations.
- [Quorum queues](https://www.rabbitmq.com/docs/quorum-queues): replicated queue option and deployment requirements.
