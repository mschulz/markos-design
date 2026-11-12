---
title: ADR-004 Event-driven Architecture Supersedes Legacy MarkOS
type: architecture-decision
status: accepted
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# ADR-004 Event-driven Architecture Supersedes Legacy MarkOS

## Decision

The owner explicitly confirmed on 12 November 2026 that the original MarkOS design is abandoned. The Agent PubSub architecture is the new product baseline. This is a replacement architecture, not a PARA/Markdown/DuckDB application with a broker attached.

- No PARA note storage, Obsidian dependency, canonical Markdown knowledge database, DuckDB foundation or desktop-first runtime is inherited.
- RabbitMQ over AMQP is the selected internal event bus, replacing provisional MQTT. Google Cloud Pub/Sub remains the Gmail ingress notification transport; these are different systems. MQTT has no required role; a future device adapter would need its own scope.
- DigitalOcean / Docker Compose provides always-on services; Supabase PostgreSQL owns durable operational state and structured knowledge. Immutable source originals live in access-controlled cloud object storage, with checksums and database references. Provider selection remains open.
- Generated Markdown is a derived view/export in a standard folder if needed. No PARA tree is imposed. `markdown_root` describes such a folder only when a future export feature explicitly needs it; it is not the system's knowledge authority or required startup setting.
- Build an independent runtime from the v0.3 contracts. Do not base implementation on the imported `markos` code, its CLI/API/watcher, schema, dependency lock or knowledge models. Preserve old work and history as an archive, not as a prerequisite or acceptance specification.
- The proposed new project boundary is `services/runtime/`, package `markos_runtime`, with its own dependencies and tests. This path is a planning choice; this documentation PR creates no runtime code. Prior M0.1 artifacts may be reviewed as candidates, not inherited automatically.

GitHub remains canonical for project code/tests/infrastructure/docs, in `markos-design` under the existing governance decision. Runtime data and personal notes stay outside GitHub. No code is pushed into the separate `markos` repository: the conditional need to use it has not arisen. A repository relocation would be a separate explicit change.

## Evidence and precedence

Sources are the [Agent PubSub Architecture chat](chatgpt-conversation://6aefc392-d95c-83ec-b848-4a884bfa3e7c), the [v0.3 Space review draft](https://chatgpt.com/space/page_826f242af13c8191a92a0e9a1a96dd3f), the subsequent RabbitMQ instructions in this continuation and the owner's explicit reset instruction on 12 November 2026.

The accessible chat read returned five recent turns, including accepted cloud placement and GitHub handoff; it exposed no older-turn cursor. It does not supply the complete early design discussion. The Space draft establishes the three planes, Gmail-first agents, structured source-linked knowledge and derived Markdown. Its MQTT and undecided-hosting text is stale: later owner decisions take precedence. This ADR does not claim access to missing turns or invent their contents.

This decision supersedes the old Vision's Markdown-canonical clauses, MarkOS Specification, Markdown Memory Architecture, semantic-extraction subsystem roadmap, and the import/reuse prerequisites of the earlier v0.3 plan. Prior delivery/evaluation records remain historical evidence. [[Documentation Authority Index]] classifies every Markdown document. Unchanged source snapshots are explicitly non-normative through that index.

## What remains valid

Human approval, provenance, owner/domain isolation, privacy, modular agents, transactional outbox/inbox, idempotency, durable task state, recovery and a shared interaction gateway remain requirements because the new design calls for them. Preserving an old safety test does not demonstrate these properties in the new runtime.

Broker selection is accepted. ADR-003's exact versions, queue/resource budgets and retention values remain proposed engineering choices requiring version/security verification and measured acceptance before use. This ADR is not a RabbitMQ performance or support audit.

## Consequences and limits

Do not merge PR #7 as the new-system foundation. Its naming cleanup and PR #6's contracts remain reviewable prior work; neither validates the replacement architecture. M0.0 import is archival work already preserved, not a new implementation gate. Rebaseline M0 around an independent synthetic event-to-task slice, then implement persistence, transport, execution, replay and lifecycle in bounded PRs.

Only documentation is changed here. Do not delete old source, migrate personal notes, merge, provision, spend or deploy. Detailed retrieval algorithms, object provider, privacy/source retention, notification provider and operational cost remain decisions, not reasons to fall back to the old architecture.

## Related

- [[Vision]]
- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 M0 Implementation Plan]]
- [[ADR-003 RabbitMQ Internal Event Bus]]
- [[ADR-0002 GitHub as Canonical Project Record]]
