---
title: Graphify Evaluation
type: architecture-evaluation
status: evaluated
version: 1.0
owner: Mark Schulz
created: 2026-08-12
updated: 2026-08-12
reviewed: 2026-08-12
tags:
  - architecture
  - graphify
  - knowledge-graph
  - provenance
---

# Graphify Evaluation

## Decision

Adopt Graphify conditionally as a replaceable semantic-discovery provider behind
a MarkOS-owned adapter. Do not make Graphify the canonical index, the source of
truth for Obsidian links, or a direct vault writer.

MarkOS retains responsibility for deterministic discovery, exact Obsidian link
resolution, source fingerprints, provenance validation, PARA proposals,
controlled writes, audit, undo and health. Graphify may supply semantic entities,
communities, paths, inferred relationships and research-question candidates.

This is a conditional architecture decision. A current-version semantic pilot
must pass the acceptance gates below before implementation work in M13 or M14
depends on Graphify.

## Package Boundary

The official Graphify Labs distribution is `graphifyy` on PyPI; its command is
`graphify`. The evaluation pinned version `0.9.40` in an isolated temporary
environment. MarkOS should invoke a pinned CLI or MCP service through an adapter,
not import undocumented Graphify internals throughout the codebase.

The machine also had a global `graphify` command whose package was `0.9.5` while
its installed skill was `0.9.38`. This mismatch demonstrates why MarkOS must own
version detection, pinning and compatibility checks.

## Evaluation Safety

The live vault was treated as read-only. The three initial concept folders were
copied to `/private/tmp/markos-graphify-eval-20260812/corpus`; nested Git data and
existing `graphify-out/` operational artifacts were excluded. The corpus held
235 files and occupied 218 MiB.

No new document content was sent to an external LLM. No local Ollama, LM Studio
or llama.cpp backend was available, so the fresh run was limited to Graphify's
deterministic Markdown extractor. A complete older semantic Graphify run already
stored beneath `Localization Research/graphify-out/` was copied and inspected
without regenerating it.

The selected live-vault folders had the same SHA-256 manifest fingerprint before
and after the evaluation:

`5ee488ea35967975f33a550f4b6d48da83fad38a19e6ff6198d7cf52818040e3`

## Fresh Structural Trial

Graphify 0.9.40 processed 47 non-operational Markdown files with no failed
sources.

| Measure | Result |
|---|---:|
| Markdown files | 47 |
| Nodes | 934 |
| Edges | 1,173 |
| Document reference edges | 286 |
| Resolved document reference edges | 177 |
| Unresolved document reference edges | 109 |

The graph supported useful local `query` and `explain` operations. Headings became
addressable nodes, paths retained file and line locations, and an add/remove probe
correctly introduced and then removed one document node and one resolved link.

### Obsidian Link Difference

MarkOS reported 266 outgoing links for the selected live folders: 208 resolved
and 58 unresolved. Graphify's parser deliberately resolves extensionless
wikilinks relative to the source note's directory. Obsidian and MarkOS can
resolve vault-wide titles and paths. In this corpus Graphify resolved 31 fewer
references, including links from `outputs/` notes to notes under `knowledge/`.

The two parsers also differ in link occurrence deduplication and accepted
Markdown constructs. Graphify therefore cannot replace the MarkOS link resolver
for backlink repair, migration planning or health decisions.

## Existing Semantic Run

The older Graphify graph under `Localization Research/graphify-out/` demonstrates
the value of semantic extraction without making a new disclosure. Its current
artifacts contain:

| Measure | Result |
|---|---:|
| Source files represented in `graph.json` | 92 |
| Nodes | 349 |
| Edges | 490 |
| PDF-backed nodes | 184 |
| Inferred edges | 128 |
| Ambiguous edges | 0 |
| Nodes without a source location | 130 |

It surfaced useful communities, cross-paper paths and research candidates. One
example connected synthetic-aperture Wi-Fi localization with later mmWave radar
odometry through coherent RF ranging rather than RSSI. The query interface made
the supporting nodes and the inferred nature of the connection visible.

The run also exposed material limitations:

- 130 nodes have no page or line location and cannot meet MarkOS evidence rules.
- Generated `graphify-observations` content was re-ingested and became a source
  of further graph concepts, creating a generated-memory feedback path.
- Equivalent concepts were sometimes split into duplicate nodes, including the
  recorded Ubicarse example and repeated UnLoc landmark concepts.
- The report says the corpus check covered three files while `graph.json`
  contains 92 distinct source files; another generated observation records a
  different 109-file, 299-node state. Operational health cannot trust the report
  without independent validation.
- The report records zero model tokens despite containing semantic and inferred
  extraction, so provider-cost metadata is not sufficient for MarkOS auditing.

## Revised Responsibility Split

```text
Obsidian vault (canonical Markdown and raw evidence)
        |
        +-- MarkOS deterministic index
        |     files, hashes, metadata, exact links and health
        |
        +-- Graphify semantic graph (external, disposable)
              entities, communities, paths and inferred relations
                         |
                         v
              MarkOS proposal and validation layer
       PARA filing, managed links, source notes, knowledge reviews
                         |
                         v
                    human approval
```

Graphify output belongs outside the vault, beneath MarkOS application support.
Generated notes, reviews and Graphify reports must be excluded from the evidence
corpus or placed in a separately labelled overlay so they cannot become primary
support for later generated claims.

## Milestone Impact

### Keep Unchanged

- M11 controlled PARA filing and safe vault-write boundary.
- M12 immutable artifacts, hashing, page-aware extraction and grounded source notes.
- M15 permissions, native UI and shared application services.
- M16 scheduling, notifications and distribution.

### Simplify or Refactor

- M13 can use a `GraphProvider` abstraction for semantic query, neighbours,
  paths, communities and inferred relationships instead of implementing a
  custom semantic graph engine.
- M14 can use Graphify graph deltas and communities as inputs to weekly research
  proposals, while MarkOS independently verifies source locations, hashes and
  exact links.
- The Mac app may display or open Graphify's generated HTML visualization.
- DuckDB can remain a narrower deterministic catalogue and Obsidian-link index;
  duplicated generic traversal code may be retired only after parity tests.

## Integration Rules

1. Pin Graphify and verify its runtime, skill and graph schema versions.
2. Integrate through a MarkOS-owned CLI or MCP adapter.
3. Keep all Graphify outputs and caches outside the vault.
4. Maintain an explicit corpus allowlist and exclude operational/generated output.
5. Preserve `EXTRACTED`, `INFERRED` and `AMBIGUOUS` confidence in every proposal.
6. Resolve every proposed factual relationship back to current source hashes and
   page or line evidence before it can enter a knowledge note.
7. Never allow a Graphify edge to cause a file move, backlink edit or vault write
   without a MarkOS preview and explicit approval.
8. Fall back to deterministic MarkOS search and graph operations when Graphify is
   absent, stale or incompatible.

## Remaining Pilot

Before adopting Graphify as an M13/M14 dependency:

1. Run a current-version semantic extraction against a temporary allowlisted
   corpus using an explicitly approved local or external model.
2. Measure entity duplication, source-location coverage, model cost and graph
   reproducibility.
3. Verify add, change, rename and deletion behaviour using Graphify's supported
   incremental commands rather than only its structural cache.
4. Test a representative, copied Roam batch and measure graph size and query
   usefulness before attempting the complete legacy collection.
5. Define and test the `GraphProvider` contract against both Graphify and the
   existing DuckDB graph.

## Related

- [[Markdown Memory Architecture]]
- [[Milestones]]
- [[PARA Migration Strategy]]
- [[Mac App Architecture]]
