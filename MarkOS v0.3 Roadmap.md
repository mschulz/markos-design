---
title: MarkOS v0.3 Roadmap
type: roadmap
status: proposed
version: 2.0
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
| M3 Classification | Confidence-aware routing and review | Synthetic bills/ordinary mail/ambiguous cases route with traceable decisions |
| M4 Electricity | Bill extraction, arithmetic validation, usage/export/charge history and reports | Trace every result to sample evidence; surface uncertainty without invented savings |
| M5 Knowledge | Structured facts/entities/relationships, keyword/semantic retrieval, consolidation and optional Markdown views | Provenance, temporal updates, contradictions and domain access verified; no PARA or Markdown authority |
| M6 Notifications | Selected push adapter and authenticated deep links | Persist attempts, deduplicate, apply privacy/quiet hours and show uncertain delivery |
| M7 Operations | CLI/replay/metrics, backup/restore and replacement drills | Measured approved recovery targets; independent alerts and operation with MacBook off |
| M8 Expansion | Additional agents/integrations | Add subscriber without producer rewrite or permission bypass |

[[MarkOS v0.3 M0 Implementation Plan]] breaks M0 into reviewable slices and acceptance cases. Old M0–M16 completion records do not transfer to this sequence. Baseline backup/monitoring design belongs in M0 before real data; M7 proves and hardens it.

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
