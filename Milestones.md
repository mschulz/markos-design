---
title: Milestones
type: roadmap
status: active
version: 1.0
owner: Mark Schulz
created: 2026-08-12
updated: 2026-08-12
reviewed: 2026-08-12
tags:
  - roadmap
  - architecture
---

# Milestones

## Implemented Software Milestones

| Milestone | Outcome | Status |
|-----------|---------|:------:|
| M0 — Foundation | Project foundation and release v0.1.0 | Complete |
| M1 — Markdown Discovery | Read-only Markdown discovery and parsing | Complete |
| M2 — DuckDB Index | Rebuildable external knowledge index | Complete |
| M3 — Index Search | Read-only search and document queries | Complete |
| M4 — Link Graph | Link resolution and graph queries | Complete |
| M5 — Read-Only API | FastAPI interface over the index | Complete |
| M6 — Graph Analysis | Graph health metrics and path analysis | Complete |
| M7 — Ingestion Diagnostics | Resilient ingestion and rejection reporting | Complete |
| M8 — Index Freshness | SHA-256 source fingerprints and change detection | Complete |
| M9 — Conditional Synchronization | Conditional, transactional index rebuilds | Complete |

## M10 — Automated Read-Only Synchronization

Status: **Complete — implemented in `markos` commit `c60400d`**

### Goal

Keep the external DuckDB index current automatically while preserving Markdown
as the read-only source of truth.

### Scope

- Add a foreground `markos watch` command.
- Detect added, modified and deleted Markdown files.
- Debounce bursts of filesystem activity into a single synchronization.
- Invoke the existing conditional and transactional synchronization boundary.
- Prevent concurrent index rebuilds.
- Continue monitoring after recoverable discovery or indexing errors.
- Support clean shutdown and structured operational logging.
- Preserve the requirement that the index resides outside the Markdown vault.

### Non-Goals

- Writing to or reorganising the Markdown vault.
- Installing or managing a background operating-system service.
- Incremental mutation of individual DuckDB records.
- Adding write operations to the HTTP API.
- Building a graphical user interface.

### Acceptance Criteria

1. Adding, modifying or deleting Markdown automatically results in a current index.
2. Multiple rapid changes are coalesced into one synchronization attempt.
3. When no source changes exist, the index remains byte-for-byte unchanged.
4. Interrupted or failed rebuilds preserve the previous valid index.
5. Concurrent watcher or rebuild activity cannot corrupt the index.
6. Recoverable errors are logged and do not terminate the watcher.
7. The watcher shuts down cleanly without leaving temporary index files or locks.
8. Automated tests use temporary vaults and deterministic timing.
9. Ruff, strict mypy and the complete test suite pass.

### Design Constraints

- Markdown remains canonical and read-only to MarkOS.
- DuckDB remains an external, disposable and fully rebuildable index.
- M10 composes the M8 freshness and M9 synchronization capabilities rather than
  introducing a second indexing path.
- Watch behavior must be testable without depending on real-time sleeps or a
  user-owned vault.

### Decision

Accepted for implementation on 2026-08-12.

### Completion

Implemented and verified on 2026-08-12. The implementation provides the
foreground watcher, debounced change handling, retry behavior, clean shutdown,
and cross-process rebuild serialization without persistent lock files.

## Related

- [[Vision]]
- [[ADR-0001 Markdown is the Source of Truth]]
