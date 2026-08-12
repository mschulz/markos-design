# Changelog

All notable changes to the MarkOS Design repository will be documented in this file.

The format is inspired by *Keep a Changelog*, but organised around design milestones rather than software releases.

---

## Unreleased

### Accepted

- Added the M10 Automated Read-Only Synchronization milestone proposal.
- Accepted M10 for implementation.
- Recorded the implemented M0–M9 software milestone history.
- Accepted M11 Controlled PARA Filing.
- Accepted M12 Raw Document Ingestion and Grounded Source Notes.
- Accepted M13 Filesystem-Native Research and Markdown Concept Memory.
- Established Markdown as the durable semantic-memory layer without embeddings or vector search.
- Accepted M14 Knowledge Maintenance and Health.
- Accepted M15 Native Mac App.
- Accepted M16 Background Operations and Distribution.
- Established the read-only PARA migration-audit strategy and initial folder findings.
- Recorded weekly knowledge review and periodic index, link and provenance health checks.

### Completed

- Recorded M10 implementation in `markos` commit `c60400d`.

### Evaluated

- Evaluated Graphify 0.9.40 against a read-only temporary copy of the initial
  concept folders and the existing Localization Research semantic graph.
- Conditionally selected Graphify as a replaceable semantic-discovery provider
  while retaining MarkOS for exact Obsidian links, provenance and safe writes.
- Ran a local Ollama pilot and rejected the tested Qwen 2.5 7B variants for
  production semantic extraction after malformed output and fabricated source
  paths; recorded the required qualification and validation gates.
- Added the MarkOS graph-validation contract and recorded that `qwen3:14b`
  produced a structurally consistent control graph but failed the strict gate
  because Graphify emitted zero source-location coverage.
- Added deterministic heading-aware source mapping: the Qwen 3 control now
  passes with complete mapped provenance, while the genuine-note run remains
  rejected because Graphify omitted mandatory relationship confidence scores.
- Added constrained Graphify confidence normalization; reprocessing now lets
  both existing Qwen 3 artifacts pass structurally while preserving explicit
  normalization provenance and rejecting missing non-extracted scores.

---

## Design Milestone D0 – Foundation

### Added

- Created the `markos-design` repository.
- Established the Obsidian design vault.
- Created the initial folder structure.
- Created the GitHub repository.
- Established the documentation workflow.
- Added `Vision.md`.

### Decisions

- The design repository is the authoritative source for architectural decisions.
- Markdown is the canonical representation of all design documentation.
- Documents are reviewed before being committed.
- Accepted documents are considered stable and are only changed when new information or architectural decisions require it.

### Next

- ADR-0000 — Why MarkOS Exists.
