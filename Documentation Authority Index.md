---
title: Documentation Authority Index
type: governance
status: active
version: 1.6
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# Documentation Authority Index

## Precedence

The owner's explicit 12 November 2026 correction, represented by [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], supersedes original-design assumptions. [[Vision]], [[MarkOS v0.3 Specification]], current roadmap and M0 plan describe the replacement system. A historical statement of acceptance or delivery does not override a later supersession.

RabbitMQ is the internal bus; PostgreSQL/source objects own durable data; Markdown is a view/export with no PARA or Obsidian requirement. Project GitHub authority is different from runtime data authority. New implementation must be independent of archived `markos` code.

## Complete Markdown document inventory

This table covers every Markdown file in this PR's repository tree, including hidden review templates. Exact archived snapshots are intentionally not rewritten, so imported hashes/provenance remain valid; this explicit index makes every clause in them historical. Other obsolete designs carry in-file supersession notices. Current instructions must not link to an archive as their implementation specification.

| Document | Authority and disposition |
|---|---|
| [PULL_REQUEST_TEMPLATE](.github/PULL_REQUEST_TEMPLATE.md) | Current review template with independent-architecture check |
| [ADR-0002 GitHub as Canonical Project Record](ADR-0002%20GitHub%20as%20Canonical%20Project%20Record.md) | Accepted project-source governance; runtime data is separate |
| [ADR-002 Cloud-first Deployment](ADR-002%20Cloud-first%20Deployment.md) | Accepted placement; earlier MQTT wording amended by ADR-003 |
| [ADR-003 RabbitMQ Internal Event Bus](ADR-003%20RabbitMQ%20Internal%20Event%20Bus.md) | Selected RabbitMQ transport; exact operating profile remains proposed |
| [ADR-004 Event-driven Architecture Supersedes Legacy MarkOS](ADR-004%20Event-driven%20Architecture%20Supersedes%20Legacy%20MarkOS.md) | Accepted owner decision: replacement architecture and independent runtime |
| [ADR-005 Governed MCP Access for Agents](ADR-005%20Governed%20MCP%20Access%20for%20Agents.md) | Proposed agent tool integration boundary, permissions/isolation/approvals/audit and future MCP acceptance gates; no runtime enablement |
| [AGENTS](AGENTS.md) | Current agent instructions; overrides historical project memory |
| [CHANGELOG](CHANGELOG.md) | Chronological evidence; earlier accepted plans may now be superseded |
| [CLAUDE](CLAUDE.md) | Current pointer to shared instructions |
| [Documentation Authority Index](Documentation%20Authority%20Index.md) | Current authority classification for all Markdown documentation |
| [Graphify Evaluation](Graphify%20Evaluation.md) | Historical experiment evidence; no requirement to reuse its stack |
| [MarkOS Cloud Deployment Architecture](MarkOS%20Cloud%20Deployment%20Architecture.md) | Current cloud requirements; detailed targets/providers remain proposed |
| [MarkOS Specification](MarkOS%20Specification.md) | Superseded original design; retained historical content, not instructions |
| [MarkOS v0.2 Project Memory](MarkOS%20v0.2%20Project%20Memory.md) | Exact archived source snapshot; not applicable agent instructions |
| [MarkOS v0.2 User Guide](MarkOS%20v0.2%20User%20Guide.md) | Exact archived source snapshot; all instructions and paths non-normative |
| [MarkOS v0.3 M0 Implementation Plan](MarkOS%20v0.3%20M0%20Implementation%20Plan.md) | Current M0-A–M0-F plan; A01–A20 evidence disposition, partial A13/A17–A19 and explicit production gates |
| [MarkOS v0.3 M0.0 Import Record](MarkOS%20v0.3%20M0.0%20Import%20Record.md) | Historical preservation/provenance evidence, not a foundation prerequisite |
| [MarkOS v0.3 M0.0 Review](MarkOS%20v0.3%20M0.0%20Review.md) | Historical import review, not new-system architecture acceptance |
| [MarkOS v0.3 Roadmap](MarkOS%20v0.3%20Roadmap.md) | Current delivery proposal; supersedes import-first/reuse path |
| [MarkOS v0.3 Specification](MarkOS%20v0.3%20Specification.md) | Current event-driven specification; detailed contracts remain reviewable |
| [Markdown Memory Architecture](Markdown%20Memory%20Architecture.md) | Superseded Markdown-canonical design; retained historical content |
| [README](README.md) | Current entry point and implementation/archive boundary |
| [Semantic Graph Extraction Roadmap](Semantic%20Graph%20Extraction%20Roadmap.md) | Superseded subsystem plan; no automatic new-system milestone transfer |
| [Semantic Graph Extraction](Semantic%20Graph%20Extraction.md) | Historical experiment evidence; no authority over new retrieval design |
| [Architecture Document Template](Templates/Architecture%20Document%20Template.md) | Current document template with authority/supersession requirement |
| [Vision](Vision.md) | Current event-driven product vision; replaces Markdown-canonical vision |
| [README](config/README.md) | Current legacy/new configuration separation; no required note root |
| [README](infrastructure/README.md) | Current new-runtime infrastructure boundary; no deployment |
| [README](src/markos/README.md) | Current archive boundary; no new product implementation in legacy package |
| [README](tests/README.md) | Current distinction between archived regressions and new acceptance |
| [MarkOS v0.3 M0-A Contracts and Configuration](MarkOS%20v0.3%20M0-A%20Contracts%20and%20Configuration.md) | Current independent M0-A implementation/evidence and explicit limits |
| [MarkOS v0.3 M0-B Durable Storage](MarkOS%20v0.3%20M0-B%20Durable%20Storage.md) | Current storage implementation, role/migration boundaries and real-database evidence |
| [MarkOS v0.3 M0-C RabbitMQ Transport](MarkOS%20v0.3%20M0-C%20RabbitMQ%20Transport.md) | Current transport, identity/topology trust and real-broker evidence; later recovery gates explicit |
| [MarkOS v0.3 M0-D Execution and Scheduling](MarkOS%20v0.3%20M0-D%20Execution%20and%20Scheduling.md) | Current synthetic lease/execution/scheduling evidence; real agents, approvals and production gates explicit |
| [MarkOS v0.3 M0-E Replay and Retention](MarkOS%20v0.3%20M0-E%20Replay%20and%20Retention.md) | Current synthetic replay/source/deletion/retention evidence, real volume-loss drill and production/privacy limits |
| [MarkOS v0.3 M0-F Lifecycle and Closeout](MarkOS%20v0.3%20M0-F%20Lifecycle%20and%20Closeout.md) | Current synthetic lifecycle, CI, restore fencing and production readiness limits |
| [MarkOS v0.3 M0 Runbook](MarkOS%20v0.3%20M0%20Runbook.md) | Current controlled test procedure and future production recovery requirements; no deployment authorization |
| [Independent runtime README](services/runtime/README.md) | Current independent project commands, dependencies and preflight limits |

## Prior branch documents and PRs

PR #6's `MarkOS v0.3 M0.1 Contracts and Configuration.md` and PR #7's `Markdown Root Usage.md` live on prior feature branches, not this main-based correction. They are historical/candidate material, not missing current specifications. No new runtime must inherit `markos.runtime`, its preflight contracts, the original root lock or legacy naming adapters without an explicit fit review.

PR #7 is superseded as the foundation route; preserve its code/history for review without merging it as the new architecture. M0.0 import remains recoverable. No deletion or source-repository rewrite is needed.

## Source coverage and remaining uncertainty

The accessible Agent PubSub chat returned five recent turns and no older cursor. It confirms cloud placement and the GitHub handoff but is not a full transcript. The Space specification supplies the event-driven design and derived-Markdown policy; later instructions replace its MQTT and undecided-hosting text. The owner's present instruction explicitly settles abandonment of the old design. The source Space draft remains unchanged and is not the current canonical specification.

Unsettled implementation choices include source provider/retention, detailed retrieval algorithms, model/push providers, remaining agent/provider dependencies and operating cost/recovery evidence. Do not reintroduce the abandoned architecture to fill those gaps.

## Validation and retained M0 evidence

All 38 Markdown documents are classified. Local links in current/template documents resolve; superseded records retain historical links and statements as evidence. The two exact source snapshots, import manifest, archived root package metadata, source/tests and dependency lock are unchanged. The archived application's 145 tests, Ruff, strict mypy (38 files) and lock check pass; these results show archive preservation only. Independent M0-A through M0-E now has 294 cases: 177 unit/boundary, 103 PostgreSQL, 13 broker/database and one dedicated broker-volume-loss drill. A01–A12 and A14–A16 have synthetic evidence; A13 has database-failure evidence. M0-F adds lifecycle/restore-generation tests and an independent container harness; its record distinguishes partial A17–A19 operational evidence from production gates. Real provider/action approvals, cloud erasure/restore, VM boot/capacity and independent monitoring remain open. Local broker absence is explicit; required CI runs both services. See [[MarkOS v0.3 M0-A Contracts and Configuration]].

## MCP documentation update

ADR-005 adds a proposed MCP access boundary, leaving the accepted event/data architecture and existing M0 scope intact. The current inventory, including later runtime slices, classifies all 38 Markdown documents. The documentation gate checks current/template metadata and local links. This update changes architecture documentation and adds isolated documentation-check tooling/tests plus a read-only GitHub Actions workflow; application source, legacy/runtime tests, locks, import manifest and exact archived snapshots are unchanged. MCP acceptance tests are not implemented or run by this update. The merged M0-A–M0-C implementation/evidence above remains intact; that documentation slice changed no runtime code or locks; subsequent implementation evidence is listed above. Earlier regression counts above remain historical evidence, not new results.

The proposed SDK/resource/audit profile is specified in ADR-005; first real MCP enablement requires MCP-01–MCP-10, while native adapters remain independent. Documentation CI validates inventory, current metadata/local paths and changed whitespace; synthetic checker tests cover broken/stale/duplicate inventory entries, links, root-scoped wikilinks, path escape and missing metadata. External URLs, heading anchors, MCP interoperability and runtime behavior are outside that check. Branch protection is not modified.
