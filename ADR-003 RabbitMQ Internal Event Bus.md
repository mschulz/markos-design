---
title: ADR-003 RabbitMQ Internal Event Bus
type: architecture-decision
status: proposed
version: 1.0
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

Propose RabbitMQ over AMQP 0-9-1 for MarkOS internal events and durable work handoff, replacing the provisional Mosquitto/MQTT baseline. The owner requested this proposal on 12 November 2026. It remains subject to PR review and bounded M0 approval; no infrastructure or application is deployed by this record.

Preserve accepted [[ADR-002 Cloud-first Deployment]]: a DigitalOcean VM runs Docker Compose services and Supabase PostgreSQL holds durable operational state. The MQTT references in that earlier ADR describe the previous provisional transport, not a requirement to retain it. This proposal changes that transport only; managed RabbitMQ or a multi-node cluster would require a separate placement/cost review.

MQTT may later serve device/Home Assistant adapters with explicit topic-to-event mapping. Google Cloud Pub/Sub still supplies Gmail mailbox-change notifications to the ingestion bridge; it is a separate ingress service.

## Motivation

MarkOS needs independently buffered subscribers, competing task workers, explicit durable handoff and inspectable failures. RabbitMQ's queue/exchange model fits this proposed workflow more directly than treating MQTT delivery acknowledgement as business completion. PostgreSQL remains the durable authority for events, tasks and recovery; changing brokers does not remove the transactional outbox or deduplication.

## Routing and domain boundaries

Propose a topic exchange per domain, `markos.{domain}.events`, with `v1.{category}.{event}` routing keys, such as `v1.email.ingested`. Declare durable queues and bindings for each logical subscribing service/domain before publishing. Electricity processing and audit receive separate queues; replicas of one service compete on its queue.

Freeze names, compatible event schemas, intended-consumer dispatch records and provisioning permissions in M0. Isolate environments and domains with reviewed virtual-host/resource permissions; grant publishers and consumers only their required exchange/queue access. Workers cannot create arbitrary bindings across domains. Validate owner/domain in application code as well. Transport naming alone does not enforce isolation. Keep sensitive bodies in protected source storage and publish bounded metadata/references.

## Publication and durable handoff

A PostgreSQL transaction commits domain changes with an outbox event. Publish persistent messages using publisher confirms and mandatory routing. A negative confirmation, returned unroutable message or unknown connection outcome keeps the entry pending; retry with its original event ID. Record dispatch only after positive confirmation and no return. Mandatory publishing detects zero matching queues; separately audit that every required subscriber binding exists.

A consumer commits inbox identity `(logical consumer, domain, event_id)` and a durable task or short business result before manual acknowledgement. A crash after commit but before acknowledgement causes harmless redelivery through deduplication. Long-running agent work is claimed from PostgreSQL with leases/fencing; task completion is separate from broker handoff. Separate business operation keys prevent duplicate effects. Ordering across workers is not guaranteed; define entity sequencing where needed.

## Retry and recovery

Use the persistent database scheduler for bounded exponential backoff/jitter and attempt limits; never build an immediate nack/requeue loop. Before acknowledging invalid or exhausted work, persist bounded diagnostics and a source reference in an access-controlled quarantine. A dead-letter/review queue aids operations, but its delivery is not the only record. Validate the selected version/queue type's dead-letter safety rather than assuming lossless forwarding.

Retain event payload references, intended-consumer dispatch and processing state long enough to meet the approved recovery policy. Broker storage loss can remove positively confirmed work. Reconciliation must identify missing per-consumer handoffs and republish even previously confirmed events, using original IDs and business keys. Archived originals and retention limits must support that recovery; define the supported replay window before production. Reconcile approvals and external effects before resuming. Replay suppresses side effects by default.

These are at-least-once workflows. Neither publisher confirms nor consumer acknowledgements promise exactly-once business actions.

## Hosting and operational limits

Use a persisted broker volume, recoverable topology/policies, TLS, restricted management access and per-service credentials. Define queue limits, prefetch, message size, disk/memory alarms and backlog thresholds before launch. Keep scheduler, outbox and recovery monitors independent of consumer progress.

The accepted single-VM topology has downtime on VM failure. Queue type and versions remain open: durable classic queues are a possible single-node pilot; quorum queues are the replicated option for a future reviewed multi-node topology. Putting multiple containers on one VM does not remove that VM's failure risk. No managed broker, cluster, resource sizing or new spending is selected here.

## Alternatives considered

- Retain Mosquitto/MQTT internally: feasible with durable application machinery, but less direct for independent service queues and explicit work handoff; retain MQTT as an optional device adapter.
- Use only PostgreSQL polling: fewer dependencies and viable at small scale; not selected for this proposal because the planned architecture calls for routed asynchronous event distribution.
- Use RabbitMQ as the event archive or business database: rejected; retained PostgreSQL events, dispatch and task records remain required for recovery.
- Add RabbitMQ Streams, a managed broker or a cluster immediately: deferred pending replay/availability requirements, topology and cost review.

## Acceptance before implementation and launch

Approve the M0 contract, then implement synthetic tests for confirm/return handling, unknown publish outcomes, independent subscribers and competing workers, domain/owner denial, database commit before ack, bounded poison review, restart persistence and complete broker-storage-loss replay. Prove no lost durable work or double-applied business effects within the approved retention window. Include side-effect suppression and expired-approval tests. M7 rehearses recovery and operational targets. None of these runtime tests has been executed in this documentation change.

## Consequences

### Benefits

Logical subscribers have explicit backlogs and failure boundaries. Additional workers share service work without changing producers. Broker delivery and durable task lifecycle become separately observable.

### Costs

RabbitMQ adds memory/disk planning, permissions, topology management, upgrades and operational monitoring. Database reconciliation and idempotency are still necessary. Single-VM availability and cross-system transaction gaps remain.

## Related

- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 Roadmap]]
- [[MarkOS Cloud Deployment Architecture]]
- [[ADR-002 Cloud-first Deployment]]
- [[ADR-0002 GitHub as Canonical Project Record]]

## References

Official documentation checked on 12 November 2026. MarkOS topology, lifecycle and acceptance requirements above are proposed design choices.

- [RabbitMQ exchanges](https://www.rabbitmq.com/docs/exchanges): exchange-to-queue routing.
- [Publisher confirms and consumer acknowledgements](https://www.rabbitmq.com/docs/confirms): separate publisher/broker and consumer/broker confirmations.
- [Dead-letter exchanges](https://www.rabbitmq.com/docs/dlx): failure routing and safety considerations.
- [Quorum queues](https://www.rabbitmq.com/docs/quorum-queues): replicated queue option and deployment requirements.
