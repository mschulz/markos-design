---
title: MarkOS v0.3 Roadmap
type: roadmap
status: proposed
version: 1.3
owner: Mark Schulz
created: 2026-11-10
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - governance
---

# MarkOS v0.3 Roadmap

The next increment establishes a reviewable project baseline for the proposed Gmail-first event system. Existing software and knowledge designs remain available while the owner resolves the differences below. No runtime milestone is approved by this repository-organization change.

## Existing baseline

Inspection of `main` at `f68edf216551fc43e4f24e492294e7a288c32530` found one branch, no open pull requests, and a documentation-only tree. [[MarkOS Specification]] is document version 2.1, draft, reviewed 13 August 2026. The old README and changelog record M0–M10 delivered in a separate `markos` repository, including M10 commit `c60400d`, and M11–M16 accepted. Those implementation claims have not been reverified against that repository in this increment.

[[Vision]] and the accepted [[Markdown Memory Architecture]] govern Markdown knowledge with a disposable DuckDB index. [[Semantic Graph Extraction]] records exact-quote grounding, deterministic citation parsing, reproducibility limits and known entity-rejection and alias-resolution gaps. [[Semantic Graph Extraction Roadmap]] remains the subsystem plan; [[Graphify Evaluation]] remains a historical evaluation. None of these files is deleted or reclassified as an implemented v0.3 component.

## Reconciliation before implementation

| Area | Existing design | v0.3 proposal | Required review |
|---|---|---|---|
| Knowledge authority | Human-readable Markdown is canonical; indexes rebuild from it | Structured facts and immutable sources; generated Markdown is a view | Decide the authoritative knowledge representation, export and recovery guarantees; amend Vision and memory ADR deliberately if needed |
| Retrieval | Lexical and graph tools; no embeddings or vector database | Keyword and semantic retrieval | Define whether semantic retrieval means graph traversal or embeddings; do not assume a vector-store decision |
| Product and interfaces | Vault/PARA workflow, CLI, read-only API, planned PySide6 Mac app | Gmail-first services, web/PWA, shared gateway, CLI, optional messaging | Define which existing services can be reused; decide whether desktop plans continue, pause or change |
| Deployment | Prior Mac-oriented background operation | Accepted DigitalOcean VM / Docker Compose; Supabase durable state; MacBook development/admin only | Specify secure networking, sizing, cost and recovery; follow [[MarkOS Cloud Deployment Architecture]] before provisioning |
| Storage and delivery | Filesystem originals and disposable DuckDB | PostgreSQL, object storage, proposed RabbitMQ/AMQP outbox/inbox | Specify data ownership, migrations, event contracts, operational recovery and privacy before implementation |
| Evidence and safety | Exact quotes, hashes, disclosure consent, preview and confirm | Provenance, domain isolation, scoped tools and approvals | Carry forward deterministic grounding and separate external-disclosure permission; define review state for structured facts |
| Milestones | Earlier M0–M16, including delivered work | New M0–M8 sequence | Always label new work “v0.3 M0”, etc.; no prior completion transfers automatically |
| Project workflow | Separate repos; design commits directly to main | GitHub canonical, code and docs together, PR review | Apply [[ADR-0002 GitHub as Canonical Project Record]]; inventory separate implementation before importing |

Governance and [[ADR-002 Cloud-first Deployment]]'s DigitalOcean/Docker Compose/Supabase operational baseline are accepted. Other runtime architecture differences remain proposals. Production is independent of the MacBook; this does not settle the prior knowledge-authority or desktop-product questions. This overlay takes precedence over historical direct-to-main and separate-repository workflow descriptions for new work. It does not erase prior knowledge decisions. Leave [[Vision]] unchanged until its substantive conflict is explicitly resolved.

## Incremental repository layout

| Path | Purpose now | Growth when approved |
|---|---|---|
| Root Title Case documents | Existing architecture and new v0.3 review documents | Retain stable paths; no bulk move into docs |
| `Templates/` | Existing architecture document template | Reuse for new focused documents |
| `src/markos/` | Documented package boundary | Approved service and agent code after implementation inventory |
| `tests/` | Test strategy and synthetic-data rules | Contract, unit, integration, recovery and access-control tests with each milestone |
| `config/` | Configuration boundary | Schemas and sanitized examples; no live credentials |
| `infrastructure/` | Infrastructure boundary | Reviewed definitions and operational docs; no deployment in this increment |
| `.github/` | Pull request template | Validation workflows once executable checks and toolchain are selected |

## Repository transition

1. Review this documentation baseline and settle the architecture conflicts needed for v0.3 M0.
2. Inspect the separate `markos` repository: branches, unmerged work, package structure, dependencies, tests, licenses and existing CI. Record exact source commits and reusable services in a migration PR.
3. Choose an import method that preserves commit history and attribution. Map existing paths and commands, keep a recoverable baseline, validate the imported tests, and define rollback before changing the canonical implementation location.
4. Import only reviewed work, update links and project instructions, and verify the existing read-only behavior with synthetic fixtures. Do not copy a real vault or runtime data.
5. Introduce v0.3 contracts and runtime slices behind explicit milestone approval. Do not remove the separate repository or retire its branches as part of this handoff.

## Internal event bus proposal

[[ADR-003 RabbitMQ Internal Event Bus]] proposes RabbitMQ over AMQP 0-9-1 in place of the provisional Mosquitto/MQTT internal bus. MQTT remains optional at a device boundary; Google Cloud Pub/Sub remains the Gmail ingress mechanism. The accepted DigitalOcean VM / Docker Compose / Supabase placement is unchanged. This proposal adds no managed broker or high-availability cluster.

Proposed M0 starting profile: RabbitMQ 4.4.0, aio-pika 10.2.3 and durable classic queues. Plan 4 vCPU / 8 GiB RAM / at least 80 GiB usable SSD, with a 4-GiB broker cap, 2-GiB memory alarm and 10-GiB free-disk alarm. Bound messages at 64 KiB, each queue at 10,000 ready messages or 32 MiB with reject-publish, consumer prefetch at 10 and replay at 10 events/second. Keep 30-day routine replay, a 90-day deduplication floor and dependency holds for unfinished work. [[ADR-003 RabbitMQ Internal Event Bus]] is the detailed profile, including client settings, quotas, age/deletion gates and cleanup semantics; these are proposals, not measured capacity or applied settings. Before v0.3 M0 coding, approve the profile, exchange/queue/binding and authorization contracts, retry/quarantine behavior and broker-loss replay. Durable exchanges/queues, persistent messages, confirms with mandatory-return handling and manual acknowledgement after database handoff are required. Separate queues serve independent logical subscribers; replicas of one service share a queue. PostgreSQL retains outbox, per-consumer dispatch/inbox and business-task truth.

Add synthetic acceptance scenarios to M0: missing routing; lost publisher confirmation and duplicate retry; one subscriber offline; independent fan-out and competing workers; crash after database commit before acknowledgement; bounded poison-message quarantine; restart with persisted broker data; and complete broker-storage loss followed by replay of previously confirmed but unfinished work. Check domain/owner isolation, approval expiry and side-effect suppression throughout. Add queue-limit partial fan-out, pressure/blocked-publisher recovery and controlled-clock tests at 30/90 days, including pending-work holds, source deletion and backup-RPO limits. M7 repeats the recovery drill on the approved topology and measures backlog recovery and operational targets. These are required future tests, not completed runtime evidence.

## v0.3 delivery sequence

All milestones below are proposed and not started. Acceptance criteria are carried from [[MarkOS v0.3 Specification]] section 14. Existing M0–M16 statuses describe a different sequence.

| Milestone | Scope | Acceptance evidence required |
|---|---|---|
| v0.3 M0 Foundation | Cloud service contracts, Supabase schemas, RabbitMQ/AMQP topology and outbox/inbox, workers, persistent schedules/leases, ACLs, restart/health policies and baseline backups/alerts | Confirm/return handling, subscriber isolation, duplicates, poison review, broker outage/storage-loss replay, worker restart, concurrent claims and bounded missed-job recovery preserve durable results |
| v0.3 M1 Control plane | Authenticated minimal web chat, agent registry, configuration, activity | Inspect agent health and converse with a test agent |
| v0.3 M2 Gmail bridge | Watch renewal, history cursor, ingestion, reconciliation | Test mail appears once in durable source store and event history |
| v0.3 M3 Classification | Confidence-aware routing and manual review | Tests distinguish bills, ordinary mail and ambiguous cases |
| v0.3 M4 Electricity | Extraction, validation, history, reporting | Synthetic sample bills yield traceable figures and anomaly explanations |
| v0.3 M5 Knowledge | Source-linked facts, retrieval, weekly consolidation | Facts trace to sources and contradictions are surfaced |
| v0.3 M6 Notifications | Push adapter and authenticated deep links | Approved test alert reaches iPhone with appropriate privacy settings |
| v0.3 M7 Operations | CLI/replay, metrics, verified restore, replacement-VM runbook and failure drills | Operator recovers workflow, measures approved RPO/RTO, verifies independent alerts and MacBook-off continuity |
| v0.3 M8 Expansion | Additional agents, integrations, optional messaging | Add a new agent without modifying existing producers |

Before coding each milestone, approve its bounded scope, API/event contracts, schema compatibility and executable acceptance plan. Include automated tests and updated documentation in the same GitHub review. Use synthetic email and bills; replay must suppress external side effects. A real account, device or deployment requires separate authorization.

## Cloud implementation plan and readiness gates

The accepted topology is defined in [[MarkOS Cloud Deployment Architecture]]. All steps below are future, unstarted work under bounded milestone approval; this PR adds documentation only.

1. **Before v0.3 M0:** inventory existing implementation and preserve history; approve schedule/event/schema contracts and synthetic acceptance fixtures. Review VM region/size, Supabase production plan/connectivity, source-object provider, monitoring destination, recovery targets, recurring cost and operator ownership. Prepare a cost estimate before requesting provisioning authorization.
2. **v0.3 M0:** add reviewed Compose definitions for each long-running service, restart/health checks, RabbitMQ persistence, exchange/queue permissions and secret references. Implement Supabase migrations for schedules/runs/leases and atomic task/outbox creation, idempotency, missed-run policies and bounded recovery. Add baseline backup and independent monitor design/configuration before any real production data. Test process restarts, duplicate claims, database/broker disconnections and the internal-bus scenarios above with synthetic fixtures.
3. **v0.3 M1–M2:** use authenticated remote API access and cloud Gmail ingress; persist cursors/watch expiry and schedule renewal/reconciliation. Verify no MacBook tunnel, local token store or filesystem is a runtime dependency. Demonstrate mailbox recovery after downtime.
4. **v0.3 M3–M6:** carry retry/idempotency/domain constraints into agents, weekly consolidation and notifications. Persist provider attempts; surface uncertain deliveries and enforce approval validity when delayed jobs resume.
5. **v0.3 M7:** execute isolated restore and replacement-VM drills; validate source checksums and schedule/outbox state, fence old workers, reconcile external effects and measure recovery targets. Verify independent alert delivery during total VM failure and end-to-end jobs with MacBook off.
6. **Before production:** owner reviews acceptance evidence, cost and operational runbook, then separately authorizes provisioning/deployment. No deployment or merge is included in this continuation.

M7 hardens and proves controls first introduced in M0; backups and monitoring cannot wait until after production starts. Selecting a persistent scheduler library, source-object provider and backup tooling remains implementation work; the required behavior is specified now.

## Decisions still open

- Review the concrete RabbitMQ profile in ADR-003: proposed versions, classic queue type, resource/flow limits and 30/90-day replay/deduplication policy. Resolve image digest/dependency lock, Python interpreter compatibility, exact domain quotas/permissions, privacy/source retention, cost and capacity evidence before M0/production; choices are proposed rather than unspecified. Multi-node quorum or managed hosting is a separate availability/cost decision; one VM is not highly available.

- Deployment details within accepted cloud baseline: region, sizing, plan/cost, secure networking, recovery targets and operational ownership. Hosting is no longer a local/cloud/hybrid question.
- Shared knowledge service versus agent-private stores and domain isolation.
- First push provider: Pushover or Home Assistant Companion.
- Messaging priority: Slack, Matrix, Telegram or native-only.
- Model providers, cost ceilings, external-disclosure consent and privacy policy.
- Source storage provider, retention, deletion and recovery guarantees.
- Confirm electricity as the first specialist workflow or choose another.
- Resolve Markdown authority, semantic retrieval and desktop direction from the reconciliation table.
- Inventory and choose the history-preserving implementation migration.
- Confirm intended repository visibility: GitHub reported `public` on inspection, while the prior conversation called it private. Do not store sensitive runtime data or change visibility in this PR.

## Related

- [[ADR-003 RabbitMQ Internal Event Bus]]

- [[ADR-002 Cloud-first Deployment]]
- [[MarkOS Cloud Deployment Architecture]]

- [[ADR-0002 GitHub as Canonical Project Record]]
- [[MarkOS v0.3 Specification]]
- [[MarkOS Specification]]
- [[Markdown Memory Architecture]]
- [[Semantic Graph Extraction Roadmap]]
