---
title: Milestones
type: roadmap
status: active
version: 2.0
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

## M11 — Controlled PARA Filing

Status: **Accepted — implementation not started**

### Goal

Introduce an explicit, default-deny vault-write boundary that can file notes
from a configured in-vault inbox into human-approved PARA destinations.

### Scope

- Configure inbox, Projects, Areas, Resources and Archives paths within the vault.
- Propose a PARA category, concept and destination for an inbox note.
- Prefer an existing concept folder and propose a new folder only when needed.
- Preview all directory creation and file moves before applying them.
- Require explicit confirmation for every initial write operation.
- Limit mutation sources to the configured inbox and destinations to configured PARA roots.
- Recheck the source hash immediately before applying a proposal.
- Reject collisions, symlinks, traversal and paths outside configured boundaries.
- Record an audit entry and support undo for successful filing operations.
- Synchronize the external index after a successful operation.

### Non-Goals

- Editing note contents.
- Moving arbitrary existing notes outside the inbox.
- Silent autonomous filing.
- Raw document extraction or summarization.
- Installing a background write service.

### Acceptance Criteria

1. Vault writes are disabled unless explicitly configured.
2. Suggestion and preview commands never modify the vault.
3. Only files currently inside the configured inbox can be filed.
4. Destinations remain within configured PARA roots and no existing file is overwritten.
5. A changed source hash invalidates the proposal before any write occurs.
6. Folder creation and moves are recoverable, audited and undoable.
7. A failed filing operation preserves the original inbox item.
8. The external index is current after a successful filing operation.
9. Tests use temporary vaults and verify files outside the allowed paths remain unchanged.

## M12 — Raw Document Ingestion and Grounded Source Notes

Status: **Accepted — implementation not started**

### Goal

Ingest raw documents from the vault inbox while preserving the original artifact
and creating a source-grounded Markdown summary suitable for later research.

### Scope

- Support PDF as the first raw document type.
- Identify files by content type and SHA-256 rather than trusting extensions alone.
- Preserve the original bytes beneath the selected concept folder's `raw/` directory.
- Extract page-aware text locally and support an optional OCR fallback for scanned documents.
- Normalize extracted text into a rebuildable intermediate representation outside the vault.
- Create a Markdown source note beneath the concept folder's `sources/` directory.
- Link material summary claims to pages or locations in the original artifact.
- Detect duplicate artifacts by source hash.
- Support configurable local or external summarization providers.
- Require separate, explicit permission before sending document content to an external provider.
- Treat document text and model output as untrusted input and validate structured output locally.
- Record source hash, generation status, review status, provider and model provenance.

### Non-Goals

- Treating an LLM summary as primary evidence.
- Modifying or optimizing the original artifact.
- Automatically accepting generated claims without evidence.
- Cross-source concept synthesis.
- Embeddings or vector search.

### Acceptance Criteria

1. The stored raw artifact is byte-for-byte identical to the inbox artifact.
2. Source notes link to the raw artifact and cite page-aware evidence for material claims.
3. External disclosure is disabled by default and separately approved from vault writes.
4. API credentials never appear in repository configuration, logs or generated notes.
5. Duplicate source hashes do not create duplicate raw artifacts without approval.
6. Failed extraction, summarization or filing leaves the inbox artifact recoverable.
7. Low-quality extraction is reported rather than hidden behind an overconfident summary.
8. Changing a raw source hash invalidates all derived extraction and source-note state.
9. The generated source note is clearly marked machine-generated and unreviewed.

## M13 — Filesystem-Native Research and Markdown Concept Memory

Status: **Accepted — implementation not started**

### Goal

Allow an LLM to research the Markdown vault through explicit lexical and graph
tools, then write provenance-preserving Markdown as durable concept memory,
without embeddings or a vector database.

### Scope

- Provide narrow tools for text, title and metadata search; note reading; wikilinks;
  backlinks; and graph traversal.
- Let the research agent iteratively search, inspect promising notes, follow links and
  refine queries instead of loading the entire vault into one context window.
- Use DuckDB only as an optional rebuildable accelerator for lexical and graph queries.
- Generate concept-memory Markdown that synthesizes grounded source notes and human notes.
- Link factual conclusions to source or human notes and ultimately to original evidence.
- Distinguish source-supported facts, cross-source synthesis, model inference and contradiction.
- Mark generated memory with source hashes, generation status and review status.
- Detect stale memories when their recorded sources change or new relevant sources appear.
- Preview new memories and refreshes as reviewable Markdown diffs before writing.
- Support both local and explicitly approved external LLM providers.

### Non-Goals

- Embedding generation, semantic vector search or a vector database.
- Treating generated memories as independent evidence.
- Silently overwriting human-authored notes.
- Autonomous publication of generated conclusions as reviewed knowledge.
- Loading the entire vault into every model request.

### Acceptance Criteria

1. The research workflow operates without embeddings or vector similarity.
2. Every factual paragraph in a generated memory links to a source or human note.
3. Generated memories are excluded as independent evidence in later research.
4. Inferences are labelled and contradictions between sources remain visible.
5. Invalid or fabricated references are rejected before a memory can be written.
6. Memory creation and refresh are preview-only until explicitly confirmed.
7. Human-authored notes are never overwritten by generated output.
8. Source-hash changes make dependent memories visibly stale.
9. Research traces identify the searches, notes and evidence used to construct the memory.
10. The complete workflow remains inspectable through ordinary Markdown and filesystem tools.

## Sequencing Decision

M11 establishes the safe write boundary. M12 builds raw-source provenance on
that boundary. M13 consumes grounded Markdown from M12 and the existing vault to
create concept memories. Implementation proceeds in this order; later milestones
must not bypass safeguards established by earlier ones.

The accepted architecture is documented in [[Markdown Memory Architecture]].

## Related

- [[Vision]]
- [[Markdown Memory Architecture]]
- [[ADR-0001 Markdown is the Source of Truth]]
