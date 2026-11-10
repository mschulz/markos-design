---
title: MarkOS v0.3 Specification
type: specification
status: proposed
version: 0.3
owner: Mark Schulz
created: 2026-11-10
updated: 2026-11-10
reviewed: 2026-11-10
tags:
  - architecture
  - governance
---

> Imported from the [MarkOS v0.3 review draft](https://chatgpt.com/space/page_826f242af13c8191a92a0e9a1a96dd3f) on 10 November 2026. The complete draft follows unchanged. v0.3 is the proposed event-driven system revision; it is not a downgrade of the existing specification's document version 2.1. Its M0–M8 labels are a new sequence, separate from the earlier M0–M16.
>
> Project governance is recorded in [[ADR-0002 GitHub as Canonical Project Record]]. Reconciliation with [[Vision]], [[MarkOS Specification]] and [[Markdown Memory Architecture]], repository layout and approval gates are in [[MarkOS v0.3 Roadmap]]. Inclusion in GitHub records this proposal; it does not approve the provisional technology choices or authorize runtime implementation.

# MarkOS v0.3 — System Specification (Review Draft)

**Status:** Proposed, not approved for implementation  
**Date:** 10 November 2026  
**Design intent:** Gmail-first, MQTT-based, no Obsidian dependency.

## 1. Vision and scope

MarkOS is an event-driven personal operating system. It ingests information, preserves original evidence, coordinates specialist AI agents, maintains durable contextual knowledge, and reports findings or proposes actions. It must remain usable through a browser, CLI, and optional messaging applications. The initial vertical slice is Gmail → classification → electricity bill processing → knowledge update → user notification.

### Non-goals for initial release

No autonomous switching of energy retailers, sending emails, making purchases, or changing Home Assistant devices without explicit approval. No general-purpose autonomous agent marketplace. No migration or modification of the existing Obsidian vault. No native iOS app in the first release.

## 2. Architecture

Three planes:

- **Control plane:** authenticated API, conversation gateway, agent registry/configuration, approval inbox, notification preferences, web UI and CLI.

- **Execution plane:** Gmail ingestion bridge, MQTT 5 broker, durable event outbox, task workers, specialist agents, schedulers and notification gateway.

- **Data plane:** PostgreSQL operational records, source document/object store, structured knowledge, full-text/semantic retrieval, provenance and audit.

MQTT is for event distribution and agent coordination, **not** the sole record of truth. Transactional outbox and durable processing state are required. A task is not completed simply because an MQTT delivery was acknowledged.

## 3. Technology baseline (provisional)

- Python for service and agent workers; typed schemas and tests.

- Eclipse Mosquitto with MQTT 5, TLS, per-service credentials and topic ACLs.

- PostgreSQL for users, domains, messages, events, outbox, inbox deduplication, tasks, agents, approvals, knowledge metadata and audit.

- Versioned JSON event payloads; object storage for original attachments.

- HTTPS REST API with SSE or WebSockets for streamed browser updates.

- Responsive web UI/PWA; CLI uses the same authenticated API.

- Notification provider adapter (Pushover or Home Assistant Companion initially; selection pending).
All infrastructure choices should be replaceable through clear interfaces.

## 4. Domain isolation and identity

Domains: personal, research, work. Every message, document, event, task, fact and conversation is scoped to a domain and owner. Cross-domain transfers require explicit policy and audit. Agents have least-privilege identities, capabilities, topic ACLs, storage permissions and bounded tool access. Work data must not silently mix with personal data.

## 5. Event contract

Every event includes: event_id (UUID), schema_version, event_type, domain, source, occurred_at (UTC), correlation_id, causation_id, trace_id, payload_ref or bounded payload, sensitivity, and optional expiry. Topic convention: `markos/v1/{domain}/{category}/{event}`. Versioned schema registry, strict validation, backward compatibility rules and dead-letter handling are required. Events may be delivered more than once; consumers deduplicate by event_id and operation key. No credentials or full sensitive email bodies in MQTT messages.

### Delivery semantics

PostgreSQL transaction persists domain changes and an outbox entry. Publisher forwards outbox to MQTT with QoS 1; consumer writes inbox/dedup and durable task state before acknowledgement. Retries use exponential backoff; poison events go to dead-letter review. A reconciliation worker detects gaps and stalled processing. Retained MQTT messages are not used as an event archive.

## 6. Gmail integration

- Gmail OAuth with least-privilege scopes and secure token handling.

- Gmail API watch to Google Cloud Pub/Sub; renew before expiry.

- Pub/Sub notification indicates mailbox change, not email contents.

- Bridge uses Gmail history IDs to retrieve changes incrementally, handles duplicate and out-of-order notifications, and reconciles gaps/full resync if history is unavailable.

- Persist message IDs, thread IDs, metadata, content/attachment references, processing status and provenance.

- Ignore or safely handle spam, deletions, labels, drafts, malformed attachments and large files.

- Initial policy: read-only Gmail access; no automated sending or deleting.

- Publish a generic ingestion event; classifier later publishes domain-specific events.

## 7. Agent contract and lifecycle

Agent manifest: ID/version, description, owner/domain, capabilities, input subscriptions, output event types, required tools, data permissions, model policy, resource budget, timeout, retry policy, health check and human approval requirements. Lifecycle: registered → enabled → healthy/degraded/disabled. Agent execution is bounded, cancellable, observable and restartable. An agent cannot create or enable another agent without approval.

### Initial agents

**Email classification agent:** classify incoming mail, produce confidence and rationale, detect bills and attachments, route events, and send uncertain classifications to review.  
**Electricity agent:** parse bills, normalise billing periods, validate arithmetic, maintain charges/usage/export history, detect anomalies and propose plan reviews; do not fabricate tariff comparisons or claim savings without data.  
**Knowledge agent:** extract source-grounded observations, maintain entities and relationships, resolve contradictions, preserve temporal context and publish updates.  
**Coordinator:** routes direct/general user requests to agents, aggregates responses, enforces permissions; does not bypass approvals.

## 8. Knowledge and documents

Preserve immutable source originals with checksums and access labels. Store extracted observations separately from generated summaries. Each fact has source reference, extraction method, observed time, validity period, confidence and supersession state. Search supports keyword and semantic retrieval, filtered by domain. Generated Markdown summaries are views, not canonical truth. Weekly consolidation reports new topics, changes, unresolved contradictions and source links. No Obsidian-specific APIs or folder conventions are required.

## 9. Interaction Gateway

Single service manages identity, conversations, agent selection, messages, tool execution progress, streaming replies, approvals and shared conversation history. Interfaces:

- **Web/PWA:** overview, conversations, agents, activity, knowledge, approvals, configuration, integrations, diagnostics.

- **CLI:** agent status, events, replay, test, inspect, configure through authenticated APIs.

- **Optional messaging adapter:** Slack or equivalent; external messages mapped to internal conversation IDs, with explicit privacy policies.
User can address a named agent or general coordinator. Agents may initiate conversations through findings/notifications. Conversations persist across interfaces.

## 10. iPhone notifications

Notification Gateway consumes notification-request events and checks domain, severity, preferences, quiet hours, deduplication and privacy. Persist delivery attempts, status and associated conversation/action. Provider adapters for Pushover and/or Home Assistant Companion; final selection open. Push messages contain minimal sensitive detail and deep-link to authenticated MarkOS view. iOS background MQTT subscriptions are not relied upon for timely delivery. Delivery and read acknowledgement are distinct; urgent action still needs escalation/reconciliation.

## 11. Configuration and approvals

Configuration is centrally stored, validated, versioned and auditable. Admin UI covers agent manifests, topics, integrations, model selection, budgets, notification rules, credentials references and domain permissions. Secrets never appear in MQTT or logs. Consequential actions (send email, purchase, modify devices, alter financial accounts, external publication) require explicit scoped human approval, with expiry, one-time tokens and complete audit. Default permissions are read-only.

## 12. Reliability, security and observability

TLS and broker authentication, secrets management, encrypted backups, database migrations, access-control tests, audit logs, trace IDs, structured logs, metrics, alerts, rate limits, LLM prompt-injection defenses, untrusted document handling, data retention and deletion policies. Backups and restore drills cover PostgreSQL, source files and configuration. Provide replay tooling with side-effect suppression and sandbox test mode. Document threat model for email attachments, external links, malicious event payloads and agent tool permissions.

## 13. First end-to-end workflow

1. Gmail receives an Amber bill.

2. Pub/Sub triggers Gmail bridge; bridge fetches and stores the source message/attachment.

3. Durable event is published to MQTT.

4. Classifier identifies electricity bill; uncertain cases go to review.

5. Electricity agent extracts period, totals, usage, exports and charges; validates and stores results.

6. Knowledge service updates source-linked account history.

7. Comparison report is created, with evidence and uncertainty.

8. Notification Gateway sends concise iPhone alert and deep link.

9. User opens MarkOS conversation to inspect, question or approve follow-up research.
All stages must survive duplicate notifications and service restarts.

## 14. Milestones and acceptance criteria

**M0 Foundation:** broker, PostgreSQL, schemas, outbox/inbox, worker runtime, ACLs, tests. Acceptance: duplicate delivery and broker outage produce no lost or double-applied work.  
**M1 Control plane:** authenticated minimal web chat, agent registry, configuration and event activity. Acceptance: inspect agent health and converse with test agent.  
**M2 Gmail bridge:** watch, renewal, history cursor, ingestion and reconciliation. Acceptance: test mail appears once in durable source store and event history.  
**M3 Classification:** confidence-aware routing and manual review. Acceptance: test suite distinguishes bills, ordinary mail and ambiguous cases.  
**M4 Electricity:** extraction, validation, history and reporting. Acceptance: sample bills yield traceable figures and anomaly explanations.  
**M5 Knowledge:** source-linked facts, retrieval, weekly consolidation. Acceptance: facts trace to sources and contradictions are surfaced.  
**M6 Notifications:** push adapter and authenticated deep links. Acceptance: test alert reaches iPhone with appropriate privacy settings.  
**M7 Operations:** CLI, replay, metrics, backup/restore, failure drills. Acceptance: operator can recover interrupted workflow.  
**M8 Expansion:** additional agents, integrations and optional messaging. Acceptance: new agent added without modifying existing producers.

## 15. Decisions awaiting review

1. Hosting topology: local, cloud, or hybrid; network security and uptime.

2. Shared knowledge service versus agent-private stores (recommend shared service with domain isolation).

3. First push adapter: Pushover versus Home Assistant Companion.

4. Messaging adapter priority: Slack, Matrix, Telegram or native-only initially.

5. Model providers, cost ceilings and privacy policy.

6. Source document storage and retention.

7. Whether electricity is the first specialist vertical slice or whether another category should precede it.

## 16. Implementation handoff rules

Do not begin implementation until review and milestone approval. Treat this specification as a proposal. Generate ADRs for unresolved architecture choices, executable acceptance tests for each milestone, API/event contracts before coding, and a migration plan that preserves prior MarkOS design artifacts. Do not touch the Obsidian vault. Keep implementation-ready specifications version-controlled in markos-design.
