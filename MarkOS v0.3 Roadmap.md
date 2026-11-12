---
title: MarkOS v0.3 Roadmap
type: roadmap
status: proposed
version: 2.4
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# MarkOS v0.3 Roadmap

## Baseline

The event-driven product replaces the original MarkOS design. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] records the owner's decision; the older PARA/Markdown/DuckDB/desktop roadmap is superseded, not an unresolved competing baseline.

Accepted direction: cloud-first DigitalOcean / Compose, Supabase durable operational state and structured knowledge, RabbitMQ internal bus, source originals in object storage, generated Markdown as a view/export, shared gateway and owner/domain isolation. Broker versions, queue/resource/retention profile and detailed operational targets remain proposals subject to verification.

## Repository and prior work

Use `markos-design` for code, tests, infrastructure and docs together. Leave `markos` untouched. M0.0 import history is archived; preserving it was not approval to build on it. PR #6's contract/config artifacts and PR #7's naming cleanup are prior review material, not automatic prerequisites. Main still contains the legacy application; the independent new runtime is not delivered.

Propose `services/runtime/` as a separate project with its own lock and tests. The first gate forbids imports/dependencies on the old package, DuckDB or note-tree configuration. No bulk deletion or code relocation occurs in this documentation PR.

## Delivery sequence

| Milestone | Outcome | Acceptance |
|---|---|---|
| M0 Foundation | Independent contracts/config, PostgreSQL events/outbox/inbox/tasks, RabbitMQ transport, leases/schedules, replay and lifecycle | Synthetic durable workflow survives duplicate delivery, lost confirms, restarts, broker loss and missed schedules; isolated roles deny cross-owner/domain access |
| M1 Control plane | Authenticated shared gateway, minimal web chat, registry/config/activity, common CLI API | Converse with synthetic agent; inspect health; enforce scopes and approval lifecycle |
| M2 Gmail | Cloud Pub/Sub ingress, watch renewal, history cursor, source storage, reconciliation | Duplicate/out-of-order mailbox notifications create one durable source/event; recover cursor gaps |
| M3 Classification | Rules-first email routing; interchangeable Jev/LLM classifiers; authorised dispatch and durable review | Held-out comparison of rules-only, rules + Jev and rules + LLM meets agreed category targets; ambiguity, provider failures, isolation, cost/latency and decision traces verified |
| M4 Electricity | Bill extraction, arithmetic validation, usage/export/charge history and reports | Trace every result to sample evidence; surface uncertainty without invented savings |
| M5 Knowledge | Structured facts/entities/relationships, keyword/semantic retrieval, consolidation and optional Markdown views | Provenance, temporal updates, contradictions and domain access verified; no PARA or Markdown authority |
| M6 Notifications | Selected push adapter and authenticated deep links | Persist attempts, deduplicate, apply privacy/quiet hours and show uncertain delivery |
| M7 Operations | CLI/replay/metrics, backup/restore and replacement drills | Measured approved recovery targets; independent alerts and operation with MacBook off |
| M8 Expansion | Additional agents/integrations | Add subscriber without producer rewrite or permission bypass |

[[MarkOS v0.3 M0 Implementation Plan]] breaks M0 into reviewable slices and acceptance cases. Old M0–M16 completion records do not transfer to this sequence. Baseline backup/monitoring design belongs in M0 before real data; M7 proves and hardens it.

## M3 rules-first classification plan

M3 consumes the Gmail ingestion/source contracts from M2 and the registry, identity and configuration controls from M1. It builds on the independent M0 runtime; it does not change PostgreSQL authority, RabbitMQ delivery semantics or approvals. This is planned work, with no classifier integration or accuracy result claimed. [[MarkOS v0.3 Specification]] defines the routing contract.

| Slice | Required work | Review/acceptance evidence |
|---|---|---|
| M3-A Policy and evaluation fixtures | Define agent/category responsibilities, owner/domain candidate filtering, approved overrides/thread rules, sender-plus-content rules, conflicts, no-match/multiple-match and review outcomes. Version a labelled synthetic/sanitised corpus; split tuning and acceptance sets without duplicate/thread leakage. Agree category-specific targets and budgets before the held-out run. | Bills versus retailer offers/outage notices, ordinary/unsupported mail, changed threads, multiple topics, missing evidence, misleading senders and instruction-like input have reviewed expected outcomes. Rules alone provide a measured baseline. |
| M3-B Replaceable classifier adapters | Implement one strict classification interface with Jev and LLM adapters, candidate applicability checks, score semantics, evidence references and versioned question/prompt inputs. Check provider privacy/retention before sending context; enforce minimal inputs, deadlines and cost limits. | Both adapters pass the same contract suite; malformed/unavailable responses and privacy-denied fallbacks cannot invent assignments. Jev typed answers do not require generated prose, and model confidence does not substitute for measured accuracy. No live-mail/provider integration is authorised by this plan. |
| M3-C Comparative evaluation | Run rules-only, rules + Jev and rules + LLM against the same held-out examples using fixed rule/model/question versions. Evaluate an optional, bounded stronger-model escalation separately rather than requiring both models for every message. | Publish per-category precision/recall, missed bills, wrong-agent rate, review/coverage rate, calibration where available, latency and total cost including retries/escalation. Select a backend, numerical thresholds and review policy from results; retain the alternative adapter without requiring both providers in production. If targets fail, keep affected categories in review. |
| M3-D Durable routing and review | Atomically store decisions/assignments and outbox events; enforce enabled agent capabilities, owner/domain and source access. Add explicit correction/reclassification history and deduplication. Preview decisions in shadow mode before an independently reviewed automatic-routing change. | Rule conflicts, low certainty, no applicable agent, multiple allowed agents, duplicate delivery, provider outage and budget exhaustion preserve traceable recoverable state. Rule/model changes are reviewed. No arbitrary queues, cross-domain routing, silent mail loss or approval bypass. |

Rules are the first decision stage. An AI adapter runs only for unresolved cases or explicitly configured evaluation/shadow checks. Record rule IDs, source references, backend/version, question/prompt version, score meaning and the acceptance/review reason; do not put raw mail or secrets in broker messages/logs. A user's correction must not automatically enable a new rule or permission.

Open M3 decisions: agent/category taxonomy and multi-assignment policy; verified rule evidence and override precedence; dataset and per-category error targets; Jev versus LLM provider/version and data handling; acceptance thresholds; optional escalation trigger; time/cost limits and review ownership. Define these in the reviewed M3 evaluation record before automatic routing. Provider claims and example confidence values are not acceptance results.

## Immediate plan

1. Review this architecture correction and retire PR #7 as a foundation path; preserve all history.
2. Prepare an independent runtime scaffold and revised contracts/config PR. Review PR #6 artifacts against the new specification; adapt only explicitly justified parts. Prove the dependency boundary without legacy note/config access.
3. Add durable state and migrations, then RabbitMQ handoff, then execution/scheduling, replay/retention and a synthetic end-to-end harness in separate PRs.
4. Gather actual broker/database fault evidence before declaring M0 complete. Only then advance gateway and Gmail work.
5. Before provisioning, review provider plans, region, cost, network/security, retention, monitoring and recovery targets. Deployment requires separate authorization.

## Open implementation decisions

- Independent runtime Python version and exact dependency lock; use a supported interpreter tested with selected libraries, rather than inherit the old project automatically.
- Revalidate RabbitMQ/aio-pika pins and image digest; measure ADR-003's proposed classic queue and resource/flow profile. Review 30/90-day replay/dedup and final source/privacy retention separately.
- Object-storage provider, schema/export/recovery/deletion contracts and backup-erasure handling.
- Retrieval algorithms/indexes and shared knowledge-service API. Structured knowledge is selected; a specific embedding/vector technology is not implied.
- Push adapter, model/privacy/budget policy, optional messaging priority, first-workflow details and operational ownership/cost.

PARA storage, Markdown-as-canonical knowledge and legacy-code reuse are no longer open choices. [[Documentation Authority Index]] prevents archived material from reintroducing them.
