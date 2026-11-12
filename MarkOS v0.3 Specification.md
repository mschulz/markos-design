---
title: MarkOS v0.3 Specification
type: specification
status: proposed
version: 0.3.10
owner: Mark Schulz
created: 2026-11-10
updated: 2026-11-12
reviewed: 2026-11-12
m3_routing_revision: 1.1
tags:
  - architecture
  - governance
---

> Source draft: [MarkOS v0.3 Space specification](https://chatgpt.com/space/page_826f242af13c8191a92a0e9a1a96dd3f). Later owner decisions select cloud hosting, RabbitMQ and replacement of the legacy design. The Space page remains an older MQTT draft; GitHub is canonical. See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] and [[Documentation Authority Index]].

# MarkOS v0.3 — System Specification

**Status:** Event-driven architecture selected; detailed implementation contracts/profile remain subject to review. This documentation change deploys nothing.
**Date:** 12 November 2026
**Design intent:** Gmail-first, RabbitMQ internal events, structured durable knowledge, no PARA or Obsidian dependency.

## 1. Vision and scope

The original PARA/Markdown/DuckDB design is superseded. Implement an independent runtime from this specification; the imported `markos` application is archived work, not its base. Structured knowledge and durable operational state belong in PostgreSQL; immutable originals belong in object storage. Generated Markdown is a view/export, optionally written to an ordinary folder with no PARA convention. A future `markdown_root` export setting is not a required runtime input.

MarkOS is an event-driven personal operating system. It ingests information, preserves original evidence, coordinates specialist AI agents, maintains durable contextual knowledge, and reports findings or proposes actions. It must remain usable through a browser, CLI, and optional messaging applications. The initial vertical slice is Gmail → classification → electricity bill processing → knowledge update → user notification.

### Non-goals for initial release

No autonomous switching of energy retailers, sending emails, making purchases, or changing Home Assistant devices without explicit approval. No general-purpose autonomous agent marketplace. No migration or modification of personal note folders in this increment. No PARA storage architecture. No native iOS app in the first release.

## 2. Architecture

Three planes:

- **Control plane:** authenticated API, conversation gateway, agent registry/configuration, approval inbox, MCP server/grant registry and policy, notification preferences, web UI and CLI.

- **Execution plane:** Gmail ingestion bridge, RabbitMQ broker, durable event outbox, task workers, specialist agents, governed MCP client access gateway, schedulers and notification gateway.

- **Data plane:** PostgreSQL operational records and structured knowledge, source document/object store, full-text/semantic retrieval, provenance and audit.

RabbitMQ is selected for internal event distribution and agent work handoff through AMQP 0-9-1, **not** as the authoritative event archive. Transactional outbox and durable processing state remain required. Publisher confirmation, consumer handoff acknowledgement and business-task completion are separate states. MQTT may be introduced later through an explicitly scoped device adapter; it is not the internal bus. See [[ADR-003 RabbitMQ Internal Event Bus]].

### Production placement (accepted ADR-002)

A DigitalOcean VM runs Docker Compose for the internal RabbitMQ broker, Gmail ingestion bridge, outbox/reconciliation services, agent workers, persistent scheduler, notification gateway and web API/control plane. Supabase PostgreSQL holds durable operational state. Cloud source-object storage remains a provider decision. The MacBook is only a development/admin client; shutdown, sleep or disconnection must not affect jobs. No production component may depend on a local tunnel, terminal or filesystem.

See [[ADR-002 Cloud-first Deployment]] and [[MarkOS Cloud Deployment Architecture]]. A single VM is not highly available; recovery is part of production readiness.

## 3. Technology baseline (architecture selected; implementation profile proposed)

- Independent Python service project with typed schemas and tests. Select and lock a supported interpreter/library set in M0-A; the legacy Python 3.13 toolchain is archive evidence, not a new-system dependency. See [[MarkOS v0.3 M0 Implementation Plan]].

- RabbitMQ over AMQP 0-9-1 (selected), durable exchanges/queues, persistent messages, publisher confirms, manual consumer acknowledgements, TLS and per-service permissions. Proposed profile: RabbitMQ 4.4.0, aio-pika 10.2.3 (Python 3.11+), durable classic queues. [[ADR-003 RabbitMQ Internal Event Bus]] defines limits and retention; validate pins/digest and resource sizing in M0. A single VM provides no broker high availability.

- Supabase PostgreSQL (accepted durable operational storage) for users, domains, messages, events, outbox, inbox deduplication, tasks, schedules, run history, leases, Gmail cursors, agents, approvals, conversations, notification attempts, knowledge metadata and audit.

- Governed MCP access for agent tools/data is proposed in [[ADR-005 Governed MCP Access for Agents]]. RabbitMQ remains the event bus; MCP does not replace native ingestion adapters. Propose official `mcp==2.3.0` without CLI extras, authenticated HTTPS Streamable HTTP and protocol 2026-07-28 only; independent locking, server compatibility/security review and measured acceptance remain required before enablement.

- Versioned JSON event payloads; object storage for original attachments.

- HTTPS REST API with SSE or WebSockets for streamed browser updates.

- Responsive web UI/PWA; CLI uses the same authenticated API.

- Notification provider adapter (Pushover or Home Assistant Companion initially; selection pending).
All infrastructure choices should be replaceable through clear interfaces.

## 4. Domain isolation and identity

Domains: personal, research, work. Every message, document, event, task, fact and conversation is scoped to a domain and owner. Cross-domain transfers require explicit policy and audit. Agents have least-privilege identities, capabilities, broker exchange/queue permissions, storage permissions and bounded tool access. Work data must not silently mix with personal data.

## 5. Event contract

Every event includes: event_id (UUID), schema_version, event_type, owner_id (UUID), domain, source, occurred_at (UTC), correlation_id, causation_id, trace_id, payload_ref or bounded payload, sensitivity, and optional expiry. Proposed RabbitMQ topology: one topic exchange `markos.{domain}.events` per domain, routing keys `v1.{category}.{event}` (for example `v1.email.ingested`), and a durable queue per logical subscribing service/domain. Queue names include domain and consumer identity; environments are isolated. Names and permissions must be frozen in the M0 contract. The earlier MQTT topic path is retired for internal traffic; an optional device adapter must explicitly map topics to this event envelope. Versioned schema registry, strict validation, backward compatibility rules and dead-letter handling are required. Events may be delivered more than once; consumers deduplicate by (logical consumer, owner_id, domain, event_id) and business operation key so independent services each process their copy. No credentials or full sensitive email bodies in broker messages. Validate required owner_id/domain against trusted publisher/service scope and payload/source permissions at consumption as well as broker authorization. Business operation keys are owner/domain scoped.

### Delivery semantics

PostgreSQL transaction persists domain changes and an outbox entry. Publisher sends persistent messages to RabbitMQ with publisher confirms and mandatory routing; a return, negative confirmation or unknown outcome leaves the entry pending for retry with the same event ID. Mark dispatched only after positive confirmation and no return. Declare required queues/bindings before enabling publishers and audit topology: mandatory routing detects zero matching queues, not a missing member of an expected subscriber set.

Each subscribing service has its own durable queue; workers for the same service compete on that queue. After validation, a consumer transaction commits inbox/dedup and a durable task (or short business result) before manual acknowledgement. A crash before acknowledgement can redeliver; duplicate handling acknowledges only after checking durable state. Long agent work runs from the database task under leases and fencing, with completion recorded separately; broker acknowledgement means durable handoff, not successful LLM execution. Classic queues require an application handoff watchdog: 10 seconds active work and 30 seconds total unacknowledged age including semaphore wait. On timeout/uncertain commit, close the dedicated consumer channel without acknowledgement; resolve redelivery from the inbox. See ADR-003 and [[MarkOS v0.3 M0 Implementation Plan]] for database/close budgets and saturation tests.

Database-backed retries use bounded exponential backoff/jitter and preserve operation keys; avoid immediate requeue loops. Persist poison-message diagnostics in a bounded, access-controlled quarantine and route a reference to a review queue/dead-letter exchange. Verify the selected RabbitMQ version/queue type's dead-letter safety; the database record is authoritative if transport fails. A reconciliation worker detects pending outbox, missing per-consumer work and stalled tasks. Retain source events, intended-consumer dispatch records and processing history under an approved retention policy so lost broker storage can be reconstructed, including previously confirmed events. Replay preserves original IDs and suppresses external side effects by default. No exactly-once or global-order guarantee is claimed.

## 6. Gmail integration

- Gmail OAuth with least-privilege scopes and secure token handling.

- Gmail API watch to Google Cloud Pub/Sub; renew before expiry.

- Pub/Sub notification indicates mailbox change, not email contents.

- Bridge uses Gmail history IDs to retrieve changes incrementally, handles duplicate and out-of-order notifications, and reconciles gaps/full resync if history is unavailable.

- Persist message IDs, thread IDs, metadata, content/attachment references, processing status and provenance.

- Ignore or safely handle spam, deletions, labels, drafts, malformed attachments and large files.

- Initial policy: read-only Gmail access; no automated sending or deleting.

- Publish a generic ingestion event; the rules-first classification component records an authorised routing decision and domain-specific outbox events. RabbitMQ distributes those events through reviewed bindings; it does not infer email meaning.

### Rules-first email routing (M3)

Use one classification component to decide which registered agent or agents should handle an ingested email. This is a future M3 capability, not a claim that the synthetic M0 runtime already classifies mail. Keep the existing PostgreSQL authority, RabbitMQ transport, independent runtime and owner/domain boundaries. The `m3_routing_revision` metadata versions this amendment independently of the shared document version, so parallel M0 implementation revisions remain intact.

1. **Establish scope in code.** Resolve owner/domain and permitted source access from trusted ingestion identity. Build candidates from enabled, authorised agent manifests and their declared responsibilities. Email text and model output cannot grant permissions, choose a different owner/domain or enable an agent.
2. **Apply reviewed rules first.** Version explicit user overrides, policy-approved thread assignments and verified sender plus recognised content/document patterns. A display name, sender address alone or a broad keyword is insufficient. Conflicting rules, changed thread context or missing evidence produce an unresolved outcome; they must not silently use the first match. Use a model only when rules do not establish a reliable assignment, or in a separately configured evaluation/shadow run.
3. **Evaluate an interchangeable classifier.** Support Jev and an LLM behind the same bounded classification interface. Supply only permitted, minimal email context and approved candidate descriptions. Treat email and attachments as untrusted data, not instructions. Both backends return validated category candidates and explicit applicable, no-match, uncertain or multiple-match outcomes, with available scores and evidence references. Provider/model and question/prompt versions remain attached. Jev need not generate a prose rationale; code can explain a decision from rule IDs, evidence references and typed answers. The contract must preserve each backend's score meaning rather than treating Jev confidence, option probabilities and LLM self-reported confidence as equivalent or calibrated accuracy.
4. **Accept or escalate in code.** Map categories to permitted agent subscriptions through versioned configuration. Apply per-category acceptance thresholds chosen on tuning examples and validated on the held-out acceptance set, including ambiguity between leading candidates. For Jev, evaluate whether any candidate applies separately from choosing among candidates; a forced choice is not evidence of applicability. Permit multiple assignments only when policy allows and the evidence supports each. Unknown, conflicting or insufficient results go to durable review. At most one configured escalation to a stronger model may gather a second judgment when allowed by privacy, time and cost budgets; repeated agreement is not proof of correctness. Unavailable providers, invalid responses and exhausted budgets leave recoverable review/retry state without a guessed assignment. Privacy denial must not trigger an unapproved provider fallback.
5. **Persist before dispatch.** Commit the classification decision, approved assignments and outbox entries atomically in PostgreSQL, preserving source/event identity and operation keys. Record the route policy, matched rule IDs, source references, backend/model version, input/question or prompt version, scores, acceptance/review reason and later user correction under access control. Keep raw mail, credentials and hidden reasoning out of broker messages and general logs. Reclassification is explicit and audited; duplicate delivery must not create duplicate agent work. RabbitMQ receives only authorised event types/routing keys through the normal publisher and subscription checks.

#### M0 execution and contract boundary

The open M0 implementation uses the synthetic `foundation.noop.requested` schema, fixed routing key and scoped no-op handoff. It does not implement email categories, classifier providers, a general agent registry or dynamic per-event subscription selection. M3 must add reviewed, versioned email/classification event schemas, exact routing/binding contracts and matching database validation/consumer-intent migrations before dispatching real category-specific work. Category mapping must select approved event types and persist the corresponding intended consumers; it must not merely put arbitrary agent IDs in a payload, broadcast sensitive mail to every registered consumer or relax M0 validation/ACLs. Preserve existing schema versions and synthetic regression coverage.

Classifier network calls execute from durable database tasks under database-clock leases/fences after the handoff transaction commits. The broker handler promptly acknowledges that durable handoff independently; the classifier worker owns no broker delivery and does not wait for business completion to release it. Do not hold a broker delivery or database transaction open while calling a provider, and do not call models inside a scheduler scan. Persist the versioned classification request/input reference before the call; commit the accepted decision and downstream outbox only with a current task fence, policy and source-access check. If a lease expires, a late result cannot dispatch work. An interrupted read-only inference may be repeated within a durable cost/attempt budget, but its output cannot bypass inbox/operation deduplication. Exceeding that budget leaves durable review state. M3 must extend the fixed synthetic executor explicitly; this document does not enable arbitrary handlers, provider calls or consequential effects in M0. Long-running classifier deadlines/lease renewal and shared execution capacity require their own measured M3 contract, rather than inheriting synthetic timeout values blindly.

No classifier may initiate consequential actions or bypass approvals. User corrections are reviewed input to later rule/model changes, not automatic rule promotion. Provider privacy/retention terms, model/version, budgets and numerical thresholds remain decisions for M3 evaluation; this specification selects the architecture, not a winning provider.

Evaluate three baselines on the same versioned, labelled, sanitised corpus: rules alone, rules plus Jev, and rules plus an LLM. Separate tuning examples from held-out acceptance examples and avoid duplicate/thread leakage across those sets. Include electricity bills, retailer marketing and outage notices, ordinary mail, changed thread subjects, multiple topics, unsupported categories, missing context, misleading senders and instruction-like content. Report per-category precision/recall, missed bills, wrong-agent assignments, review/coverage rate, score calibration where available, latency and total workflow cost including escalation/retries. Set acceptance targets before evaluating the held-out set; a model score is not an observed success rate. Approve a dated evaluation record and an explicit fallback/review policy before enabling automatic routing. Synthetic or sanitised fixtures are the default; any external use of real mail requires separately approved source access and provider data handling.

Design references: [TypeSafe's typed question interface](https://docs.typesafe.ai/introduction), [confidence semantics](https://docs.typesafe.ai/confidence) and [applicability checks for routing](https://docs.typesafe.ai/patterns/companion-noul). These describe the candidate API; they are not MarkOS accuracy measurements or a commitment to a provider/version. See [[MarkOS v0.3 Roadmap]] for the M3 delivery gates.

## 7. Agent contract and lifecycle

Agent manifest: ID/version, description, owner/domain, capabilities, input subscriptions, output event types, required tools, data permissions, model policy, resource budget, timeout, retry policy, health check and human approval requirements. Lifecycle: registered → enabled → healthy/degraded/disabled. Agent execution is bounded, cancellable, observable and restartable. An agent cannot create or enable another agent without approval.

### Governed MCP access (proposed)

Agents access approved MCP servers through a logical tool gateway in the independent runtime. Grants are distinct from event subscriptions and bind trusted owner/domain/principal, server identity, exact tool/schema, constrained arguments and sources, disclosure destinations, effect class and budgets. Deny by default; discovery, server annotations and existing ChatGPT/Codex connectors grant no runtime access. Resolve credentials only at the gateway, require authenticated HTTPS for remote servers and reauthorize every dispatch/retry. Stdio support and additional protocol features are deferred for separate review.

Partition credentials, calls, results, caches and any connection state by owner/domain/principal. Validate source permissions before retrieval; cross-domain transfers require explicit approved audited workflows. Treat descriptions/results as untrusted data, bound requests/results and prevent implicit nested tool calls, arbitrary egress or permission expansion.

A read-only call may disclose private data. Standing grants permit only reviewed bounded reads with defined data classes and recipients; new disclosure or consequential effects require human approval bound to an immutable, expiring, one-use call intent. Approval cannot override permissions. Persist authorization/approval consumption and dispatch audit before calls; unavailable policy/audit storage blocks dispatch. Unknown external outcomes require reconciliation, with no blind retries or exactly-once claim. Replay suppresses external effects by default.

MCP calls run from durable task execution, outside broker handoff, scheduler scans and database transactions. The current M0-A–M0-C runtime supplies only synthetic contracts, storage and transport; it enables no MCP or general tool executor. Add the gateway through a separately reviewed execution/control-plane contract, preserving the M0 task/fence, approval and outbox boundaries. Agent execution slots and gateway attempt limits are separate budgets; a tool call cannot create extra agent slots or escape its run deadline. Expired/revoked task authority blocks future gateway dispatch and local finalization; an already-issued remote effect remains subject to reconciliation. Native model adapters for rules-first M3 routing remain interchangeable and do not acquire an MCP dependency merely by using a model.

PostgreSQL stores scoped intent, approval, dispatch and result/unknown records with redacted provenance and correlation. Agents cannot rewrite audit history. [[ADR-005 Governed MCP Access for Agents]] defines transport/security controls, approval/revocation/retry semantics, audit fields and future MCP-01–MCP-10 acceptance tests. This proposal enables no MCP access and changes no M0 implementation scope.

Proposed single-VM starting profile: aggregate gateway ceiling 512 MiB/0.5 CPU; two concurrent tool attempts globally and one per agent run; 64 KiB arguments/1 MiB cumulative decoded response; 5-second connect, 10-second idle, 30-second attempt and 60-second total execution/retry deadline. Admit ten attempts/minute per owner/domain (burst two), ten attempts/run and at most 100 durable ready calls globally. Permit one proven-safe read retry; no paid synthetic tools. ADR-005 defines precise counting, streaming, authentication and backpressure rules; these values are unmeasured proposals, not active configuration.

Proposed audit retention is 365 days for minimal audit/approval metadata after the latest terminal transition or attempt, seven days for optional sensitive copies after completion/approval expiry, and 14 days for redacted diagnostic logs. Preserve minimal unresolved/held evidence and alert at 24 hours; daily cleanup and restores enforce deletion records. Audit copies do not set canonical source retention. See ADR-005 for holds, privacy deletion and the existing 7-daily/4-weekly backup proposal.

### Initial agents

**Email classification agent:** apply rules first, then a replaceable Jev or LLM classifier for unresolved mail; detect bills/attachments, persist traceable decisions, enforce authorised assignments and send uncertain/no-match/conflicting cases to review. Select the backend through M3 evaluation, not model confidence alone.
**Electricity agent:** parse bills, normalise billing periods, validate arithmetic, maintain charges/usage/export history, detect anomalies and propose plan reviews; do not fabricate tariff comparisons or claim savings without data.
**Knowledge agent:** extract source-grounded observations, maintain entities and relationships, resolve contradictions, preserve temporal context and publish updates.
**Coordinator:** routes direct/general user requests to agents, aggregates responses, enforces permissions; does not bypass approvals.

## 8. Knowledge and documents

Preserve immutable source originals with checksums and access labels. Store extracted observations separately from generated summaries. Each fact has source reference, extraction method, observed time, validity period, confidence and supersession state. Search supports keyword and semantic retrieval, filtered by domain. Generated Markdown summaries are derived views, not canonical truth. No PARA hierarchy is required; indexes and retrieval operate over structured knowledge and source references, not a legacy note tree. Weekly consolidation reports new topics, changes, unresolved contradictions and source links. No Obsidian-specific APIs or folder conventions are required.

## 9. Interaction Gateway

Single service manages identity, conversations, agent selection, messages, tool execution progress, streaming replies, approvals and shared conversation history. Interfaces:

- **Web/PWA:** overview, conversations, agents, activity, knowledge, approvals, configuration, integrations, diagnostics.

- **CLI:** agent status, events, replay, test, inspect, configure through authenticated APIs.

- **Optional messaging adapter:** Slack or equivalent; external messages mapped to internal conversation IDs, with explicit privacy policies.
User can address a named agent or general coordinator. Agents may initiate conversations through findings/notifications. Conversations persist across interfaces.

## 10. iPhone notifications

Notification Gateway consumes notification-request events and checks domain, severity, preferences, quiet hours, deduplication and privacy. Persist delivery attempts, status and associated conversation/action. Provider adapters for Pushover and/or Home Assistant Companion; final selection open. Push messages contain minimal sensitive detail and deep-link to authenticated MarkOS view. iOS background MQTT subscriptions are not relied upon for timely delivery. Delivery and read acknowledgement are distinct; urgent action still needs escalation/reconciliation.

## 11. Configuration and approvals

Configuration is centrally stored, validated, versioned and auditable. Admin UI covers agent manifests, exchanges/queues and routing policies, integrations, model selection, budgets, notification rules, credentials references and domain permissions. Secrets never appear in broker messages or logs. Consequential actions (send email, purchase, modify devices, alter financial accounts, external publication) require explicit scoped human approval, with expiry, one-time tokens and complete audit. Default permissions are read-only within explicit grants; this does not authorize private-data disclosure to new recipients. MCP approvals bind server/tool/schema, arguments, data/recipient, policy/grant versions and expiry as defined in [[ADR-005 Governed MCP Access for Agents]].

## 12. Reliability, security and observability

TLS and broker authentication, secrets management, encrypted backups, database migrations, access-control tests, audit logs, trace IDs, structured logs, metrics, alerts, rate limits, LLM prompt-injection defenses, untrusted document handling, data retention and deletion policies. Backups and restore drills cover PostgreSQL, source files and configuration. Provide replay tooling with side-effect suppression and sandbox test mode. Document threat model for email attachments, external links, malicious event payloads and agent tool permissions.

### M0-D implementation boundary

[[MarkOS v0.3 M0-D Execution and Scheduling]] implements the synthetic subset for review: inline no-op tasks, DB-clock leases/fences/global slots, durable attempts/review and versioned interval/daily schedules. Scans commit unique occurrences, events/outbox and cursor/audit together. Catch-up is bounded, coalesce/skip are audited and DST gap/fold behavior is explicit. Only fixed no-op work executes; real agents/tools, approvals, source-backed execution, daemon lifecycle and full replay/retention remain separate gates.

### Persistent scheduling

Persist schedule definitions, timezone, next due time, missed-run policy and run history in Supabase. Atomically create uniquely keyed occurrences with tasks/outbox entries and advance schedule state. Claim runs with expiring leases and fencing; recover expired claims and overdue work on startup and periodic scans. Execution is at-least-once with business idempotency, bounded retries and dead-letter review. Select catch-up, coalesce or audited skip per job, with catch-up limits and alerts; never silently lose missed jobs or rely solely on local cron. See [[MarkOS Cloud Deployment Architecture]] for transaction, timezone, outage and external-side-effect recovery requirements.

### Cloud operations and recovery

All long-running Compose services use `restart: unless-stopped`; Docker starts at VM boot. Intentionally stopped services require explicit restart. Add health checks, graceful shutdown, dependency reconnect/backoff, pinned images and persistent broker volumes. Health checks alone do not restart unhealthy containers. Supabase outages suspend unsafe claims and unrecorded side effects.

Require encrypted off-VM database backups, independent recoverable exports, separate source-object backups, configuration/secrets recovery and isolated restore drills. Verify Supabase plan retention/PITR capability before launch; database backups do not include object bytes. Monitor scheduler heartbeat, overdue runs, outbox age, ingestion lag/watch expiry, notification failures, resource usage and backup freshness. External monitoring and alert delivery must survive loss of this VM and the notification gateway.

[[MarkOS Cloud Deployment Architecture]] defines recovery procedures and proposed RPO/RTO/retention targets pending owner review. Test VM loss, database restore, broker/network outages and MacBook shutdown before production. Reconcile previously sent external effects after restore before releasing work.

## 13. First end-to-end workflow

1. Gmail receives an Amber bill.

2. Pub/Sub triggers Gmail bridge; bridge fetches and stores the source message/attachment.

3. Durable outbox event is published to RabbitMQ and routed to the classifier's durable queue; publisher confirmation is recorded separately from consumer processing.

4. Reviewed rules identify an electricity bill when evidence is sufficient; otherwise the selected Jev or LLM classifier proposes a category. Code validates the assignment and records it before dispatch; uncertain/conflicting cases go to review.

5. Electricity agent extracts period, totals, usage, exports and charges; validates and stores results.

6. Knowledge service updates source-linked account history.

7. Comparison report is created, with evidence and uncertainty.

8. Notification Gateway sends concise iPhone alert and deep link.

9. User opens MarkOS conversation to inspect, question or approve follow-up research.
All stages must survive duplicate notifications and service restarts.

## 14. Milestones and acceptance criteria

**M0 Foundation:** cloud Compose service contracts, Supabase schemas, RabbitMQ contracts, outbox/inbox, worker runtime, persistent scheduling/leases, ACLs, restart/health policies, baseline backups/alerts and tests. Acceptance: duplicates, broker outage, worker restart and overdue schedule recovery preserve durable business results; concurrent claims and bounded catch-up are verified. Test publisher confirm/return handling, lost-confirm retries, independent subscriber delivery, competing workers, crash-before-ack deduplication, poison-message review and recovery after broker storage loss from retained events/dispatch state.
**M1 Control plane:** authenticated minimal web chat, agent registry, configuration and event activity. Acceptance: inspect agent health and converse with test agent. Add MCP registry, least-privilege policy, immutable approvals and durable audit contracts with a synthetic read-only server; pass MCP-01–MCP-10, including connector-specific interoperability/recovery evidence, before enabling the first real MCP connector. Native Gmail/Pub/Sub and other non-MCP adapters do not depend on MCP acceptance.
**M2 Gmail bridge:** watch, renewal, history cursor, ingestion and reconciliation. Acceptance: test mail appears once in durable source store and event history.
**M3 Classification:** rules-first email routing with interchangeable Jev/LLM classifiers, authorised assignments and durable review. Acceptance: compare rules-only, rules + Jev and rules + LLM on held-out labelled fixtures; meet pre-agreed per-category accuracy/coverage targets, demonstrate no-match/multiple-match and failure handling, and publish measured cost/latency plus the chosen thresholds/fallback policy. See the M3 gates in [[MarkOS v0.3 Roadmap]].
**M4 Electricity:** extraction, validation, history and reporting. Acceptance: sample bills yield traceable figures and anomaly explanations.
**M5 Knowledge:** source-linked facts, retrieval, weekly consolidation. Acceptance: facts trace to sources and contradictions are surfaced.
**M6 Notifications:** push adapter and authenticated deep links. Acceptance: test alert reaches iPhone with appropriate privacy settings.
**M7 Operations:** CLI, replay, metrics, verified backup/restore, replacement-VM runbook and failure drills. Acceptance: operator recovers an interrupted workflow, measures approved RPO/RTO, verifies independent outage alerts and proves jobs continue with MacBook off. M7 repeats and hardens MCP recovery drills already required before real MCP enablement. M7 validates and hardens the operational controls introduced in M0; baseline backups/monitoring must precede any production data.
**M8 Expansion:** additional agents, integrations and optional messaging. Acceptance: new agent added without modifying existing producers. MCP-enabled agents reuse the governed boundary; each real connector needs scope, disclosure and recovery acceptance, including MCP-10, before enablement.

## 15. Decisions awaiting review

RabbitMQ is the selected internal bus in [[ADR-003 RabbitMQ Internal Event Bus]]. The proposed starting choices are RabbitMQ 4.4.0, aio-pika 10.2.3 and durable classic queues; a 4-vCPU/8-GiB/80-GiB SSD VM planning envelope; a 4-GiB broker memory cap with 2-GiB alarm; 10-GiB disk-free alarm; 64-KiB messages; per-queue 10,000 ready messages/32 MiB with reject-publish; prefetch 10; and 30-day routine replay with a 90-day deduplication floor and holds for unfinished work. See ADR-003 for exact semantics and additional bounds. Before M0, approve these choices, exact topology/permissions, image digest/dependency lock, dead-letter safety and workload validation. Routine replay retention does not change backup RPO or authorize privacy retention. Keep the accepted single-VM hosting baseline; managed service or multi-node quorum availability requires a separate topology/cost decision.

1. Hosting topology and durable operational database are resolved by [[ADR-002 Cloud-first Deployment]]: DigitalOcean VM, Docker Compose and Supabase PostgreSQL. Still review region, sizing, production plan/cost, network controls and recovery targets before provisioning.

2. Shared knowledge service versus agent-private stores (recommend shared service with domain isolation).

3. First push adapter: Pushover versus Home Assistant Companion.

4. Messaging adapter priority: Slack, Matrix, Telegram or native-only initially.

5. Model providers, cost ceilings and privacy policy. M3 must select the email classifier (Jev or LLM), pinned version, candidate taxonomy, per-category acceptance thresholds and escalation/review policy from measured results; neither provider is selected by this document.

6. Source document storage and retention.

7. Whether electricity is the first specialist vertical slice or whether another category should precede it.

8. Approval/revalidation of the MCP SDK/protocol, resource-limit and audit-retention profiles, first server/tool grants, gateway identity/credential placement, measured limits and connector reconciliation/erasure remain proposed in [[ADR-005 Governed MCP Access for Agents]].

## 16. Implementation handoff rules

M0-A, M0-B and M0-C are merged in main through PRs #10, #11 and #12. See [[MarkOS v0.3 M0-A Contracts and Configuration]], [[MarkOS v0.3 M0-B Durable Storage]] and [[MarkOS v0.3 M0-C RabbitMQ Transport]] for the slice records and evidence. The CLI remains validation-only; explicit storage/transport libraries do not constitute a deployed service. M0-D execution/scheduling remains a separate review, while full replay/recovery and production lifecycle remain later work. These merges enable no MCP, classifier provider or deployment.

[[MarkOS v0.3 M0 Implementation Plan]] defines an independent runtime, its own dependency/test boundaries, ordered M0-A–M0-F slices and A01–A20 acceptance cases. No history-preserving import or legacy regression gate is a prerequisite for the new architecture. Prior imports remain archival evidence; prior contracts require fit review before adaptation.

Review each implementation slice with contracts, automated tests and docs. Keep source-grounded knowledge, approvals, domain isolation and recovery requirements explicit. This correction authorizes no merge, provisioning, spending, deployment or personal-data migration. Preserve the old repositories and histories without building the new system on their code. Keep project artifacts in `markos-design`; use [[Documentation Authority Index]] for superseded documents.
