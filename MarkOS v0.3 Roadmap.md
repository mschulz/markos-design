---
title: MarkOS v0.3 Roadmap
type: roadmap
status: proposed
version: 2.5
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
m3_routing_revision: 1.1
tags:
  - architecture
---

# MarkOS v0.3 Roadmap

## Baseline

The event-driven product replaces the original MarkOS design. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] records the owner's decision; the older PARA/Markdown/DuckDB/desktop roadmap is superseded, not an unresolved competing baseline.

Accepted direction: cloud-first DigitalOcean / Compose, Supabase durable operational state and structured knowledge, RabbitMQ internal bus, source originals in object storage, generated Markdown as a view/export, shared gateway and owner/domain isolation. Broker versions, queue/resource/retention profile and detailed operational targets remain proposals subject to verification.

## Repository and prior work

Use `markos-design` for code, tests, infrastructure and docs together. Leave `markos` untouched. M0.0 import history is archived; preserving it was not approval to build on it. PR #6's contract/config artifacts and PR #7's naming cleanup are prior review material, not automatic prerequisites. Main preserves the legacy application and now includes merged independent M0-A–M0-C contracts, storage and transport. This is not a deployed runtime or M0 completion.

`services/runtime/` is a separate project with its own lock and tests. The first gate forbids imports/dependencies on the old package, DuckDB or note-tree configuration. No bulk deletion or code relocation occurs in this documentation PR.

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

## M0-A merged status

M0-A contracts/configuration are merged through PR #10,
with 104 tests, its own lock/CLI, schemas/examples and a clean-wheel isolation
check. See [[MarkOS v0.3 M0-A Contracts and Configuration]]. This does not deliver
M0-B–M0-F or any runtime service. PR #7 is closed unmerged; its branch is retained.

## M0-B merged status

M0-B is merged through PR #11 and adds explicit PostgreSQL events/outbox/consumer intent, atomic inbox/task
handoff, bounded conflict quarantine, scoped roles/RLS and fenced outbox claims.
Its 34 real PostgreSQL cases bring the independent suite to 145 tests. See
[[MarkOS v0.3 M0-B Durable Storage]]. This is not a live RabbitMQ worker or M0
completion. PR #10’s CI context error is fixed and its corrected hosted run passes;
PR #9 remains the separate, consistent ADR-002 correction.

## M0-C merged status

M0-C is merged through PR #12 and adds pinned RabbitMQ/aio-pika library transport, exact live topology audit,
confirms/mandatory returns, durable backoff/review outboxes and generation-fenced
consumer recovery with independent watchdogs. The required suite has 194 cases,
including 11 real broker/database cases. See [[MarkOS v0.3 M0-C RabbitMQ Transport]].
That transport slice delivers no agent worker, full broker-loss replay or production deployment.

## M0-D review status

[[MarkOS v0.3 M0-D Execution and Scheduling]] adds a fixed no-op worker, DB-clock leases/fencing and a global two-lease budget, durable attempts/retry/review and atomic interval/daily scheduling. Missed policies, timezone/DST, versions and controls are explicit/audited. The required 256-case suite includes 12 broker/database cases. Real agents/tools/approval expiry, retained-event replay and the deployed lifecycle remain later gates.

## Governed MCP access proposal

[[ADR-005 Governed MCP Access for Agents]] adds a tool/data integration boundary, not a replacement event bus. RabbitMQ, PostgreSQL authority, source objects, derived Markdown and the independent runtime remain unchanged. This documentation adds no M0 dependency, implements no MCP client and enables no personal account, credential or server. Merged M0-A–M0-C contracts/storage/transport and their acceptance evidence remain intact; later M0 slices remain separate review units. No MCP implementation is added to those slices.

| Stage | MCP increment | Gate before advancing |
|---|---|---|
| M1 Control plane | Versioned server/grant registry, trusted owner/domain identity, tool gateway contracts, disclosure/effect policy, immutable approval intents and durable audit; synthetic read-only server only | MCP-01–MCP-10 before any real MCP enablement, including connector-specific evidence: deny by default, scope isolation, safe discovery, disclosure checks, expiry/revocation/one-use approval, egress and credential protections, bounded execution and audit/crash recovery |
| M2–M4 Integrations and agents | Add a connector only where MCP meets the source permission and recovery contract; classifiers/electricity agents reuse the gateway | Separate reviewed PR per connector; MCP-01–MCP-10 with synthetic fixtures and connector-specific interoperability/recovery evidence before real MCP data enablement; Gmail watch/Pub/Sub remains native ingestion |
| M5 Knowledge | Optional scoped resource retrieval through the same policy gate; resource feature remains disabled until reviewed | Verify provenance, source access, domain separation and disclosure; resource URLs/handles grant no implicit fetch rights |
| M7 Operations | Scoped credential rotation/revocation, audit backup/deletion/holds and unknown-effect reconciliation drills | Repeat and harden MCP-07–MCP-10 already required before real MCP enablement, with restore/replay/idempotency evidence and MacBook-off cloud operation; approve audit retention independently of event replay windows |
| M8 Expansion | Additional approved servers/tools/features | Explicit grant/schema/version review and connector-specific acceptance; no wildcard access or automatic discovery enablement |

MCP-01–MCP-10 are future acceptance cases in ADR-005, not passing tests in this documentation PR. Start with bounded synthetic reads. Consequential effects and new private-data disclosures require exact human approval; incomplete policy or unavailable approval/audit storage blocks calls. Do not introduce a second approval inbox, agent-owned credential store or per-agent bypass client.

Proposed profile from ADR-005: official `mcp==2.3.0` without CLI extras; authenticated HTTPS Streamable HTTP with protocol 2026-07-28 only; 512 MiB/0.5 CPU aggregate gateway ceiling; two global concurrent attempts/one per agent run; 64 KiB arguments/1 MiB total decoded response; 5-second connect/10-second idle/30-second attempt/60-second total execution and retry budget; ten attempts/minute per owner/domain (burst two), ten attempts/run and 100 global ready calls. One proven-safe read retry, no paid synthetic tools. The implementation must test exact/one-over limits and SDK retry/OAuth behavior before enabling access.

Proposed audit profile: 365-day minimal audit/approval metadata after the latest terminal transition or attempt; seven-day optional sensitive copies after completion/approval expiry; 14-day redacted logs; protected minimal unresolved/held evidence with a 24-hour unresolved alert; daily cleanup and deletion reapplication on restore. Keep the existing 7-daily/4-weekly backup proposal. These are unmeasured proposals requiring review, not configured limits or legal retention requirements. Native non-MCP adapters gain no MCP milestone dependency; M7 drills do not postpone the first-connector safety gate.

## M0-E review status

[[MarkOS v0.3 M0-E Replay and Retention]] adds bounded retained-event recovery, suppressed replay with one-use older-event permits, synthetic source gates/deletion and staged 30/90-day operational cleanup. The 294-case acceptance set includes an actual disposable broker-volume-loss drill while PostgreSQL survives. Real object-provider/backup erasure, action approvals and deployed lifecycle/pressure/monitoring remain gates. The MCP audit-retention proposal remains separate.

## Immediate plan

1. Retain the accepted architecture correction; PR #7 is closed unmerged and its reference branch/history is preserved.
2. Preserve merged M0-A–M0-C and their independent contracts, isolation and database/broker evidence; no automatic promotion of old PR #6/#7.
3. Review M0-D execution/scheduling and M0-E synthetic replay/retention separately, then implement M0-F lifecycle evidence in its own PR.
4. Gather actual broker/database fault evidence before declaring M0 complete. Only then advance gateway and Gmail work.
5. Before provisioning, review provider plans, region, cost, network/security, retention, monitoring and recovery targets. Deployment requires separate authorization.

## Open implementation decisions

- Preserve the independently tested M0-A–M0-C interpreter/lock baseline; select remaining agent/provider/MCP dependencies through reviewed PRs, without inheriting the archived project.
- Revalidate RabbitMQ/aio-pika pins and image digest; measure ADR-003's proposed classic queue and resource/flow profile. Review 30/90-day replay/dedup and final source/privacy retention separately.
- Approve/revalidate ADR-005’s proposed MCP SDK/protocol, resource and audit-retention profiles; select the first server and gateway identity/credential placement, grants, approval expiry, control-request/non-ready storage limits, connector reconciliation, erasure/holds and operational ownership.
- Object-storage provider, schema/export/recovery/deletion contracts and backup-erasure handling.
- Retrieval algorithms/indexes and shared knowledge-service API. Structured knowledge is selected; a specific embedding/vector technology is not implied.
- Push adapter, model/privacy/budget policy, optional messaging priority, first-workflow details and operational ownership/cost.

PARA storage, Markdown-as-canonical knowledge and legacy-code reuse are no longer open choices. [[Documentation Authority Index]] prevents archived material from reintroducing them.

## M3 rules-first classification plan

M3 consumes the Gmail ingestion/source contracts from M2 and the registry, identity and configuration controls from M1. It builds on the independent M0 runtime; it does not change PostgreSQL authority, RabbitMQ delivery semantics or approvals. This is planned work, with no classifier integration or accuracy result claimed. `m3_routing_revision` versions this amendment separately so the shared document version and M0 review-status sections can advance independently. [[MarkOS v0.3 Specification]] defines the routing contract.

| Slice | Required work | Review/acceptance evidence |
|---|---|---|
| M3-A Policy and evaluation fixtures | Define agent/category responsibilities, owner/domain candidate filtering, approved overrides/thread rules, sender-plus-content rules, conflicts, no-match/multiple-match and review outcomes. Version a labelled synthetic/sanitised corpus; split tuning and acceptance sets without duplicate/thread leakage. Agree category-specific targets and budgets; tune numerical thresholds on tuning data, then freeze the configuration before the held-out run. | Bills versus retailer offers/outage notices, ordinary/unsupported mail, changed threads, multiple topics, missing evidence, misleading senders and instruction-like input have reviewed expected outcomes. Rules alone provide a measured baseline. |
| M3-B Replaceable classifier adapters | Implement one strict classification interface with Jev and LLM adapters, candidate applicability checks, score semantics, evidence references and versioned question/prompt inputs. Check provider privacy/retention before sending context; enforce minimal inputs, deadlines and cost limits. | Both adapters pass the same contract suite; malformed/unavailable responses and privacy-denied fallbacks cannot invent assignments. Jev typed answers do not require generated prose, and model confidence does not substitute for measured accuracy. No live-mail/provider integration is authorised by this plan. |
| M3-C Comparative evaluation | Run rules-only, rules + Jev and rules + LLM against the same held-out examples using fixed rule/model/question versions and thresholds. Evaluate an optional, bounded stronger-model escalation separately rather than requiring both models for every message. | Publish per-category precision/recall, missed bills, wrong-agent rate, review/coverage rate, calibration where available, latency and total cost including retries/escalation. Select a backend only if its frozen thresholds/review policy meet the pre-agreed targets; retain the alternative adapter without requiring both providers in production. If targets fail, keep affected categories in review. Retuning after inspecting acceptance failures requires a fresh held-out set. |
| M3-D Durable routing and review | Extend the synthetic M0 event schemas, exact subscriptions/bindings and database consumer-intent validation through reviewed versioned contracts/migrations. Execute classifier calls from leased durable tasks outside broker handoff, scheduler scans and database transactions. Atomically store decisions/assignments and outbox events only with a current fence; enforce enabled agent capabilities, owner/domain and source access. Add explicit correction/reclassification history and deduplication. Preview decisions in shadow mode before an independently reviewed automatic-routing change. | Rule conflicts, low certainty, no applicable agent, multiple allowed agents, duplicate delivery, provider outage and budget exhaustion preserve traceable recoverable state. Rule/model changes are reviewed. An expired lease/late result cannot dispatch work; no network call extends broker handoff or holds a transaction open. Preserve M0 synthetic contracts, ACLs and regressions. No arbitrary queues, unintended consumers, cross-domain routing, silent mail loss or approval bypass. |

Rules are the first decision stage. An AI adapter runs only for unresolved cases or explicitly configured evaluation/shadow checks. Record rule IDs, source references, backend/version, question/prompt version, score meaning and the acceptance/review reason; do not put raw mail or secrets in broker messages/logs. A user's correction must not automatically enable a new rule or permission.

Open M3 decisions: agent/category taxonomy and multi-assignment policy; verified rule evidence and override precedence; dataset and per-category error targets; Jev versus LLM provider/version and data handling; acceptance thresholds; optional escalation trigger; time/cost limits and review ownership. Define these in the reviewed M3 evaluation record before automatic routing. Provider claims and example confidence values are not acceptance results.
