---
title: Semantic Graph Extraction
type: architecture-specification
status: in-progress
version: 1.0
owner: Mark Schulz
created: 2026-08-13
updated: 2026-08-13
reviewed: 2026-08-13
tags:
  - architecture
  - knowledge-graph
  - provenance
  - semantic-extraction
  - citations
---

# Semantic Graph Extraction

This is the successor to [[Graphify Evaluation]], which ended by rejecting both the
tested local model (Qwen 3 14B) and Graphify's `claude-cli` backend on
reproducibility and confidence-contract grounds, and recommending: *"the next
provider experiment should use a MarkOS-owned extraction contract with explicit
conditional confidence rules, canonical entity identity and a stability-oriented
prompt."* This document specifies that contract, what has been tested against
it, what it still gets wrong, and the design decisions made along the way — so
they can be revisited deliberately rather than re-discovered.

## Purpose

Extract a knowledge graph (entities and relationships) from the Markdown vault
that a person can trust without independently re-verifying every claim against
the source. The two failure modes already demonstrated in [[Graphify
Evaluation]] are fabrication (inventing entities, paths or edges that are not in
the source) and non-reproducibility (the same input producing materially
different output on repeated runs). Both must be addressed structurally, not by
asking a model to try harder.

## Goals

- Every entity and relationship in the accepted graph must be traceable to an
  exact quote at an exact location in a real, hash-verified source file.
- Untrusted provider output is validated in code before anything is written;
  a graph that fails validation is rejected outright, not repaired or guessed.
- Prefer deterministic, local computation over a provider call wherever the
  content is already structured enough to parse in code.
- Keep the provider-facing surface (what content leaves the machine, and to
  whom) minimal, explicit, and gated behind its own consent flag — separate
  from vault-write permission, per [[Markdown Memory Architecture]].
- Treat reproducibility as a measurable, reportable property, not an assumption.

## Non-Goals

- This is not a general-purpose RAG or vector-search system; per [[Markdown
  Memory Architecture]], MarkOS does not use embeddings or a vector database
  anywhere in the vault-facing pipeline.
- This is not a temporal or continuously-evolving memory graph (contrast with
  Graphiti's agent-memory model, evaluated and rejected below) — the vault is
  static Markdown, re-extracted on demand, not a stream of incoming episodes.
- This does not aim for a fully unattended pipeline. Per [[Markdown Memory
  Architecture]]'s generated-note lifecycle, extracted graphs remain reviewable
  and are not authoritative until a person has looked at them.

## Architecture Decisions

### 1. A MarkOS-owned extraction contract, not raw provider output

`extract-semantic-graph` sends one prepared chunk at a time to Claude with:

- Claude tools and session persistence disabled, JSON-Schema-constrained
  structured output, low reasoning effort by default (chosen to reduce latency
  and semantic drift on this structured task).
- A strict evidence contract: every entity's `canonical_label` must be an exact
  substring of its own quoted `evidence`, and `evidence` must itself be an exact
  substring of the cited chunk's line range. Anything that fails either check
  raises immediately — the whole extraction is rejected, not repaired.
- Conditional confidence handling: `EXTRACTED` relationships are fixed at
  `confidence_score: 1.0` by policy (never invented if the model omits it, per
  the confidence-normalization rule already established against Graphify);
  `INFERRED` and `AMBIGUOUS` scores must come from the model, within fixed
  bands, or the relationship is rejected.
- Only `EXTRACTED` relationships are kept in this stable evidence graph.
  `INFERRED` and `AMBIGUOUS` relationships are rejected with a recorded code,
  not silently dropped or downgraded — inference belongs in a separate,
  explicitly reviewable workflow, not this contract.
- Deterministic post-processing: entities are deduplicated by a canonical
  identity key (kind + casefolded, punctuation-stripped label), stable node IDs
  are derived by hash, and every mention is retained even after dedup.

**Known gap:** a single entity failing the evidence check aborts the *entire*
extraction, discarding every other valid entity and relationship in the batch.
Relationships already have a reject-and-continue path (rejected items are
recorded with a code and location); entities do not. Confirmed in practice: one
reproducibility run (below) hard-crashed on exactly this before succeeding on
retry. This is an accepted, not yet fixed, gap.

### 2. Deterministic citation parsing (no provider call)

A `markos.citations` module parses `## References` sections directly, using
Markdown-it's own token tree (not regex on raw text) to distinguish italic
titles from author lists, so citation-style Markdown formatting is read the way
it renders, not the way it happens to be typed. It extracts `(authors, title,
venue, year)` per bulleted reference, plus exact source line grounding, with
zero provider involvement and zero possibility of fabrication — it is
extractive parsing of real bytes, not generation.

This targets the common `Author, I., Author, I. *Title.* Venue, Year.` list
format specifically; it is not a general bibliographic parser, and lines that
don't match are skipped rather than raising, since personal notes vary in
citation style.

### 3. Skip chunks fully covered by parsed citations

`extract_semantic_graph` classifies each prepared chunk: if every non-blank,
non-heading line in it is covered by a parsed citation's line range, the chunk
is never sent to Claude at all. Its citations are instead converted directly
into graph entities — one `publication` entity per citation (label = title) and
one `person` entity per author — reusing the *same* dedup, mention-merging and
grounding-validation pipeline as provider output, just sourced deterministically
instead of from a model call. Grounding validation was widened to cover every
prepared chunk (not only the ones actually sent), since these deterministic
entities legitimately cite a chunk Claude never saw.

Deliberately no relationships are synthesized from citations: there is no
`RelationKind` for authorship, and nothing in a References section identifies
which entity in the note is doing the citing. Extending the contract to cover
that is future work, not an oversight.

**Verified end-to-end against the real vault** (`Active-Badge.md`, re-chunked at
800 characters so the References section isolated into its own chunk): 9 total
chunks, 1 correctly skipped, 8 sent to Claude for real. The skipped chunk never
appeared as a provenance source anywhere in the output graph, and its 5 authors
plus 2 publication titles — previously missing from every model-only run below —
appeared correctly, with correct chunk-local line grounding, at zero additional
provider cost.

### 4. Rejected alternative: adopt an existing knowledge-graph framework

Considered and rejected: [Graphiti](https://github.com/getzep/graphiti),
LlamaIndex's `PropertyGraphIndex`, LangChain's `LLMGraphTransformer`, and
Microsoft GraphRAG. None of them provide the exact-quote grounding contract
above — they constrain the *shape* of extracted triples (via a schema), not
that each triple is a literal, located quote from a real source file. Adopting
one would mean giving up working, purpose-built verification to gain a larger,
more opinionated dependency that still doesn't solve the actual problem.

Graphiti specifically: built for temporal, continuously-evolving agent memory
(ingesting conversational "episodes," invalidating superseded facts over time)
and requires a stateful graph database (Neo4j or FalkorDB) plus embeddings for
its hybrid retrieval — both cut against the local-first, no-vector-database,
disposable-index architecture this project has already committed to.

Kùzu (an embedded, file-based graph database, architecturally the "DuckDB of
graphs") remains a plausible future storage-layer upgrade over the current
in-memory `networkx` usage, but is unrelated to the extraction/grounding
problem this document addresses and has not been prototyped.

## Experiment Log

Numbers before this document's creation date are carried forward from
[[Graphify Evaluation]] for continuity; everything from the MarkOS-owned
contract onward was run directly against `extract-semantic-graph`, not through
Graphify.

| Provider / configuration | Node overlap | Edge overlap | Verdict |
|---|---:|---:|---|
| Qwen 3 14B, 4 narrow chunks, via Graphify | 46.2% (6/13) | 23.1% (3/13) | Fail — not reproducible |
| Claude Sonnet, 4 chunks, via Graphify | 33.3% (6/18) | 20.0% (4/20) | Fail — not reproducible, confidence contract violated |
| **MarkOS-owned contract**, whole file as one chunk | **77.3% (17/22)** | **60.0% (3/5)** | Improved, still not a clean pass |

### MarkOS-owned contract reproducibility retest

Two runs of `extract-semantic-graph` against the real, unmodified
`Active-Badge.md`, whole file as a single chunk, default settings:

- Run 1: 22 nodes, 3 edges.
- First attempt at run 2 **crashed the entire extraction** — one entity's
  evidence quote didn't exactly match its cited source text (the known gap in
  Decision 1). Retried once.
- Run 2 (retry): 17 nodes, 5 edges, 1 rejected relationship.
- Overlap: 77.3% node, 60.0% edge — better than either prior provider
  combination, but the entities missing from run 2 were specifically the 5
  citation-only authors, i.e. exactly the content later made deterministic by
  Decisions 2–3.

Real cost: 3 Claude Code CLI calls, ≈265k input tokens combined (dominated by
fixed per-call schema/prompt overhead — a single whole-file call costs roughly
the same ≈88k input tokens regardless of the file's actual size, since the
JSON Schema and instructions are resent every time).

### Chunk-size sensitivity and cost

Re-chunking the same file at 800 characters to isolate the References section
(Decision 3's verification) produced 9 chunks instead of 1, and cost roughly 8×
a single whole-file call (≈774k input / ≈14k output tokens across 8 real
calls) — because the fixed per-call overhead dominates regardless of chunk
size. Finer chunking trades cost for isolation; there is no free way to get
both with the current one-call-per-chunk design.

### GLiNER prototype (local, zero-shot NER — not adopted, not integrated)

Prototyped [GLiNER](https://github.com/urchade/GLiNER)
(`gliner-community/gliner_small-v2.5`) as a possible local, cost-free,
non-generative alternative for the entity layer specifically. Run in an
isolated scratch environment only; never added to the project's real
dependencies.

- Every one of 142 test entities (3 notes) had an exactly matching character
  span in the source — unlike a generative model, an extractive span-tagger
  cannot fabricate an entity that isn't literally present, by construction.
- Found the same 5 citation authors as the deterministic parser, plus real
  prose entities (people, organizations, places), at 0.83–0.99 confidence, for
  zero API cost.
- **Real gaps, not threshold-tunable:**
  - No relation extraction in the base model (a separate model, GLiREL /
    gliner-relex, would be needed for edges).
  - No judgment filtering — repeatedly tags generic common nouns (`radio`,
    `ultrasonic`, `wireless`) as entities; the Claude prompt explicitly excludes
    these, GLiNER has no equivalent instinct.
  - Worse span segmentation than the purpose-built parser on the exact
    structure it targets: split `"Want, R."` into two separate entities
    (`Want`, `R.`) instead of one.
  - The small model silently truncates at 768 tokens with only a logged
    warning — on `Active-Badge.md` this dropped the entire `## Relationships`
    section (≈25% of the file) unseen, not merely unextracted.
  - Inconsistent even on citations: correctly extracted the 1st and 3rd
    reference titles as `publication` entities, but fragmented the 2nd into
    just `"Active Badges"` mistyped as `technology` — same structural pattern,
    one of three missed.

**Cross-check against a real Claude run on the identical note** (canonicalized
with MarkOS's own identity rule): 11 entities both systems agreed on, 11
Claude-only, 24 GLiNER-only. Most of the disagreement was canonicalization
choice, not fabrication — Claude normalized citation authors to bare surnames
(`Costanza`) while GLiNER kept the exact cited text (`Costanza, R.`); both are
defensible, but they don't collide as the same entity without an alias-
resolution step. Genuine GLiNER-side gaps: missed
`Indoor-Location-Sensor-Technologies` entirely and mistyped
`Context-Aware-Computing` as `technology` instead of `concept`.

**Verdict:** not a replacement for either the citation parser (less consistent
on the one structure it's built for) or the Claude pass (no relations, no
filtering judgment, silent truncation). The shared-entity set (agreed by two
independent systems) is a materially higher-confidence signal than either
system alone — the plausible integration is as a free, zero-hallucination-risk
second opinion, not a pipeline stage. Not built.

## Known Gaps and Open Questions

- **Entity-level grounding failures are fatal, not recoverable.** One bad quote
  aborts an entire extraction rather than being rejected in isolation like a
  bad relationship. Fix would mirror the existing relationship-rejection path.
- **Cross-system and cross-mention alias resolution does not exist.**
  `"Want, R."` (citation form), `"Want"` (prose form), and any future
  GLiNER-sourced form of the same person are three different canonical keys
  today. Surfaced independently by the citation-node work and the GLiNER
  cross-check — the same real gap from two directions.
- **The deterministic-parsing approach has only been extended to citations.**
  Wikilinks, frontmatter tags, and headings are equally structural and
  currently still pass through the LLM as part of mixed chunks; extending
  Decision 2's philosophy to them is unexplored.
- **No relationship layer for anything deterministic.** Citations produce
  nodes only, by design (Decision 3) — there is no `RelationKind` for
  authorship and no identified "citing" endpoint. Whether and how to add one is
  an open design question, not scheduled work.
- **Reproducibility is improved, not solved.** 77.3%/60.0% overlap on one
  file, one pair of runs, is a single data point. No threshold has been set for
  what counts as "reproducible enough" to scale beyond single-file testing.

## Consequences

### Benefits

- Every accepted graph item traces to a real, hash-verified quote — the two
  failure modes that sank both prior providers in [[Graphify Evaluation]] are
  addressed structurally, not by hoping a bigger or different model behaves.
- Citation content, wherever it's isolated into its own chunk, is now free,
  instant, and reproducible by construction — a real reduction in both cost and
  the model's chance to be wrong, on real vault content.
- The framework survey (Decision 4) closed off several plausible-looking
  detours with concrete, documented reasons, rather than leaving them as
  recurring "should we just use X" questions.

### Costs

- Per-call fixed overhead (~88k input tokens for schema and instructions alone)
  makes finer chunking expensive; isolating structural content from semantic
  content is a real cost/isolation tradeoff, not free.
- The entity-crash gap and the alias-resolution gap are both known and both
  unfixed; either could surface again in a way that looks like a regression
  before it's recognized as the same pre-existing issue.
- Extending deterministic parsing beyond citations (wikilinks, headings, tags)
  is unstarted work with no effort estimate yet.

## Related

- [[Graphify Evaluation]] — the evaluation this document continues from.
- [[Markdown Memory Architecture]] — the provenance and disclosure rules this
  contract implements.
- [[Vision]]
- [[MarkOS Specification]]
- MarkOS repository `AGENTS.md` carries a condensed, working-agreement version
  of the experiment log above, for coding agents operating in that repo
  day-to-day; this document is the fuller record.
