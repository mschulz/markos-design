# MarkOS Design

This repository contains the architectural design, engineering decisions and long-term roadmap for the MarkOS project.

The implementation of MarkOS lives in the separate `markos` repository.

---

# Current Design Milestone

## D0 — Foundation

| Document | Status |
|-----------|:------:|
| Vision | ✅ |
| ADR-0000 Why MarkOS Exists | Superseded |
| ADR-0001 Markdown is the Source of Truth | Superseded |
| Engineering Charter | Superseded |
| MarkOS Specification | ✅ |
| Home | ⬜ |

ADR-0000, ADR-0001, and Engineering Charter were never written separately;
their content is now covered by [[MarkOS Specification]] instead — the "why"
in Section 1, Markdown-as-source-of-truth across Sections 1 and 2, and
engineering practice in Section 7 — rather than as standalone documents.

Progress: **2 complete, 3 superseded, 1 remaining (Home)**

---

# Software Milestones

## Completed

| Milestone | Outcome | Status |
|-----------|---------|:------:|
| M0 — Foundation | Project, CLI, configuration, logging, domain model and tests | ✅ |
| M1 — Markdown Discovery | Read-only Markdown discovery and parsing | ✅ |
| M2 — DuckDB Index | Rebuildable external knowledge index | ✅ |
| M3 — Index Search | Read-only indexed search and document queries | ✅ |
| M4 — Link Graph | Link resolution and graph queries | ✅ |
| M5 — Read-Only API | FastAPI interface over the index | ✅ |
| M6 — Graph Analysis | Graph health metrics and path analysis | ✅ |
| M7 — Ingestion Diagnostics | Resilient ingestion with rejection reporting | ✅ |
| M8 — Index Freshness | Source fingerprints and change detection | ✅ |
| M9 — Conditional Synchronization | No-op sync when current and transactional rebuild when stale | ✅ |
| M10 — Automated Read-Only Synchronization | Foreground monitoring and serialized rebuilds | ✅ |

## Accepted — Not Yet Implemented

| Milestone | Outcome | Status |
|-----------|---------|:------:|
| M11 — Controlled PARA Filing | Human-approved filing from an in-vault inbox | Accepted |
| M12 — Raw Document Ingestion | Immutable raw artifacts and grounded source notes | Accepted |
| M13 — Markdown Concept Memory | Filesystem-native LLM research without vector search | Accepted |
| M14 — Knowledge Maintenance | Periodic health, repair and research proposals | Accepted |
| M15 — Desktop App | PySide6 interface over shared Python services | Accepted |
| M16 — Background and Distribution | Scheduling, notifications and notarized delivery | Accepted |

See [[MarkOS Specification]] for scope, acceptance criteria and the
migration plan, and [[Markdown Memory Architecture]] and
[[Graphify Evaluation]] for the governing designs and current integration
evaluation.

---

# Repositories

| Repository | Purpose |
|------------|---------|
| `markos` | Production software |
| `markos-design` | Architecture, engineering decisions and roadmap |

---

# Principles

- Markdown is the source of truth.
- DuckDB is a rebuildable index.
- Markdown is the durable semantic-memory layer; vector storage is not required.
- Generated conclusions remain linked to inspectable source evidence.
- CLI and UI use the same application services and safety rules.
- Health and knowledge suggestions are periodic and reviewable, not silently applied.
- Architecture before implementation.
- Fixed milestone scope.
- Small, releasable increments.
- AI proposes; humans decide.

---

For project documentation begin with [[Home]].
