---
title: MarkOS v0.3 Roadmap
type: roadmap
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-11-10
updated: 2026-11-10
reviewed: 2026-11-10
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
| Storage and delivery | Filesystem originals and disposable DuckDB | PostgreSQL, object storage, MQTT outbox/inbox | Specify data ownership, migrations, event contracts, operational recovery and privacy before implementation |
| Evidence and safety | Exact quotes, hashes, disclosure consent, preview and confirm | Provenance, domain isolation, scoped tools and approvals | Carry forward deterministic grounding and separate external-disclosure permission; define review state for structured facts |
| Milestones | Earlier M0–M16, including delivered work | New M0–M8 sequence | Always label new work “v0.3 M0”, etc.; no prior completion transfers automatically |
| Project workflow | Separate repos; design commits directly to main | GitHub canonical, code and docs together, PR review | Apply [[ADR-0002 GitHub as Canonical Project Record]]; inventory separate implementation before importing |

The governance decision is accepted; the runtime architecture differences remain proposals. This overlay takes precedence over historical direct-to-main and separate-repository workflow descriptions for new work. It does not erase prior knowledge decisions. Leave [[Vision]] unchanged until its substantive conflict is explicitly resolved.

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

## v0.3 delivery sequence

All milestones below are proposed and not started. Acceptance criteria are carried from [[MarkOS v0.3 Specification]] section 14. Existing M0–M16 statuses describe a different sequence.

| Milestone | Scope | Acceptance evidence required |
|---|---|---|
| v0.3 M0 Foundation | Broker, PostgreSQL, schemas, outbox/inbox, worker runtime, ACLs | Duplicate delivery and broker outage produce no lost or double-applied work |
| v0.3 M1 Control plane | Authenticated minimal web chat, agent registry, configuration, activity | Inspect agent health and converse with a test agent |
| v0.3 M2 Gmail bridge | Watch renewal, history cursor, ingestion, reconciliation | Test mail appears once in durable source store and event history |
| v0.3 M3 Classification | Confidence-aware routing and manual review | Tests distinguish bills, ordinary mail and ambiguous cases |
| v0.3 M4 Electricity | Extraction, validation, history, reporting | Synthetic sample bills yield traceable figures and anomaly explanations |
| v0.3 M5 Knowledge | Source-linked facts, retrieval, weekly consolidation | Facts trace to sources and contradictions are surfaced |
| v0.3 M6 Notifications | Push adapter and authenticated deep links | Approved test alert reaches iPhone with appropriate privacy settings |
| v0.3 M7 Operations | CLI, replay, metrics, backup/restore, failure drills | Operator can recover interrupted workflow |
| v0.3 M8 Expansion | Additional agents, integrations, optional messaging | Add a new agent without modifying existing producers |

Before coding each milestone, approve its bounded scope, API/event contracts, schema compatibility and executable acceptance plan. Include automated tests and updated documentation in the same GitHub review. Use synthetic email and bills; replay must suppress external side effects. A real account, device or deployment requires separate authorization.

## Decisions still open

- Hosting: local, cloud or hybrid, including network security and uptime.
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

- [[ADR-0002 GitHub as Canonical Project Record]]
- [[MarkOS v0.3 Specification]]
- [[MarkOS Specification]]
- [[Markdown Memory Architecture]]
- [[Semantic Graph Extraction Roadmap]]
