---
title: Markdown Memory Architecture
type: architecture
status: accepted
version: 1.0
owner: Mark Schulz
created: 2026-08-12
updated: 2026-08-12
reviewed: 2026-08-12
tags:
  - architecture
  - memory
  - provenance
  - para
---

# Markdown Memory Architecture

## Decision

MarkOS will use Markdown as its durable semantic-memory layer. An LLM may
research the vault through explicit filesystem, lexical and link-graph tools and
may propose new Markdown that records what it discovered. MarkOS will not require
embeddings, semantic vector search or a vector database.

DuckDB remains an external, disposable index. It may accelerate deterministic
text, metadata and graph queries, but it is neither the canonical knowledge store
nor an opaque semantic-memory layer.

## Motivation

Generated knowledge should remain human-readable, reviewable, linkable, portable
and independent of a particular model or database. A person must be able to
inspect the same notes and evidence used by an LLM, correct the synthesis with an
ordinary editor and preserve the result through normal filesystem and Git tools.

## Knowledge Layers

MarkOS maintains three provenance layers:

```text
Immutable original artifact
    → grounded Markdown source note
        → Markdown concept memory
```

### Original Artifacts

Raw source files are preserved byte-for-byte under a concept folder's `raw/`
directory. A SHA-256 identifier anchors all derived content to the exact source
version.

Example:

```text
30 Resources/Heat Pumps/raw/heat-pump-study.pdf
```

### Grounded Source Notes

A source note is a Markdown representation of one original artifact. It contains
a structured summary, extracted concepts and page- or location-aware evidence.
It links directly to the original and is clearly marked machine-generated and
unreviewed until a person reviews it.

Example:

```text
30 Resources/Heat Pumps/sources/Heat Pump Efficiency Study.md
```

### Concept Memories

A concept memory synthesizes multiple grounded source notes and human notes. It
records current understanding, related concepts, contradictions and open
questions. Its factual conclusions link back through source notes to primary
evidence.

Example:

```text
30 Resources/Heat Pumps/Heat Pumps.md
```

## Filesystem-Native Research

An LLM researches progressively through explicit tools:

1. Search note paths, titles, bodies and metadata lexically.
2. Read only promising notes.
3. Follow wikilinks, Markdown links and backlinks.
4. Traverse the resolved knowledge graph when useful.
5. Refine search terms from discovered language.
6. Collect claims and supporting evidence.
7. Produce a reviewable Markdown synthesis.

The entire vault is not copied into one prompt. Search and graph operations may
use the rebuildable DuckDB index or direct filesystem tools, but neither path
uses embeddings or vector similarity.

## Provenance Rules

- Raw artifacts are immutable and identified by source hash.
- Material claims in source notes cite an original page or location.
- Factual statements in concept memories link to source or human notes.
- Generated concept memories are navigation and synthesis aids, not independent evidence.
- Later research must resolve a generated claim to source or human evidence before relying on it.
- Inferences are labelled as inferences.
- Contradictions are preserved rather than silently reconciled.
- Fabricated, missing or invalid source references prevent generated output from being applied.
- A changed source hash invalidates dependent extraction, summaries and concept memories.

These rules prevent generated-memory feedback loops in which one unsupported AI
statement is repeatedly cited by other generated notes until it appears factual.

## Controlled Vault Writes

Vault writes are a new, explicit capability rather than an extension of the
read-only indexer.

- Writes are disabled by default.
- The initial mutation source is a configured inbox inside the vault.
- Destinations are restricted to configured PARA roots.
- Every proposal shows folders, moves and generated files before application.
- Initial operations require explicit human confirmation.
- Source hashes are rechecked immediately before writing.
- Existing files are never overwritten.
- Successful operations are audited and undoable.
- Failure preserves the original inbox item.
- Existing query and HTTP interfaces remain read-only.

## PARA Filing

MarkOS first selects a PARA category and then a concept container:

- Projects contain active work with a defined outcome.
- Areas contain ongoing responsibilities or standards.
- Resources contain reference material and topics of interest.
- Archives contain completed or inactive material.

MarkOS searches existing concept folders before proposing a new one. New folders
are created only when they add meaningful organization, and initial creation
requires approval.

## Raw Document Ingestion

PDF is the first supported raw format. The pipeline is:

```text
Inbox PDF
    → content-type and hash validation
    → local page-aware extraction or optional OCR
    → normalized rebuildable text
    → grounded source-note proposal
    → approved raw move and Markdown creation
```

Conversion to Markdown is an intermediate normalization step, not a replacement
for the original. The extracted representation may be cached outside the vault
and regenerated from the immutable source.

## LLM Providers and Disclosure

Local and external LLMs share a provider boundary. Permission to write the vault
does not imply permission to send content to an external provider.

- External disclosure is disabled by default.
- The user sees what content will be transmitted.
- Approval may be configured globally or requested per operation.
- Credentials remain in environment variables or a credential store.
- Document content is untrusted data, never executable instruction.
- Summarizers receive no filesystem or command-execution tools.
- Model output uses a constrained structure and all paths and references are validated locally.
- Provider and model provenance is recorded without credentials.

## Generated Note Lifecycle

Generated source notes and concept memories carry generation and review state.
They are created and refreshed through preview-and-confirm workflows. Refreshes
produce a Markdown diff, never a silent overwrite.

A human may promote a generated memory into a curated note. Once promoted,
MarkOS does not replace it automatically; later research is proposed separately
or merged only with explicit approval.

## Consequences

### Benefits

- Knowledge and memory remain open, inspectable Markdown.
- No embedding model or vector-store dependency is introduced.
- Original evidence remains available for verification.
- Generated synthesis can be reviewed, corrected and versioned normally.
- The system remains portable across editors and LLM providers.

### Costs

- Agentic lexical research may require more tool iterations than vector retrieval.
- Provenance extraction and validation add implementation complexity.
- PDF extraction quality varies, especially for scans, tables and diagrams.
- Human approval adds friction but is required while the write model matures.
- Concept-folder and memory quality require ongoing curation.

## Milestone Mapping

- M11 implements controlled PARA filing and the safe vault-write boundary.
- M12 implements raw document ingestion and grounded source notes.
- M13 implements filesystem-native research and Markdown concept memory.

## Related

- [[Vision]]
- [[MarkOS Specification]]
