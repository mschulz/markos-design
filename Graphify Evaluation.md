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

## Local Ollama Pilot

A follow-up pilot used Ollama 0.32.9 on an Apple M2 Pro with 16 GB unified
memory. Graphify remained pinned at 0.9.40 and all inputs and outputs remained in
the temporary evaluation directory. No Graphify semantic request used a cloud
provider.

Graphify's documented default `qwen2.5-coder:7b` model failed to return the
required JSON for a copied 2,515-word research note. Graphify rejected the prose
response and wrote an empty graph after 52 seconds. The general instruction model
`qwen2.5:7b` failed in the same way after 41 seconds.

A short controlled note succeeded:

| Measure | First run | Repeat run |
|---|---:|---:|
| Nodes | 5 | 5 |
| Edges | 4 | 4 |
| Duration | 27.1 s | 23.8 s |
| Input tokens | 883 | 883 |
| Output tokens | 556 | 556 |

The two `graph.json` files were byte-for-byte identical, with SHA-256
`73c8f0ac71107a894afd9069549627df34451080749ed2b8a08abc38565c0865`.
This demonstrates useful determinism when the model follows the extraction
schema.

The shortest genuine knowledge note in the copied corpus, `Active-Badge.md` at
654 words, completed in 60 seconds and produced six nodes and four edges. Its
content was not trustworthy:

- it invented `src/auth/session.py`, which does not exist in the corpus;
- it classified fabricated nodes as code entities;
- three edges referred to source node identifiers absent from the graph;
- it supplied no line locations; and
- it labelled unsupported semantic associations as `EXTRACTED`.

Graphify accepted this structurally valid but semantically invalid JSON. A
MarkOS corpus-allowlist and endpoint validator would reject the fabricated paths
and dangling edges, confirming that Graphify output must remain untrusted input.

The `--token-budget` option did not split the 2,515-word Markdown file because
Graphify only pre-splits files above its separate internal large-file threshold.
Reducing the token budget therefore did not reduce the observed 2,050-token
request. Reliable use with this class of local model would require either a
larger capable model or MarkOS-owned heading-aware preprocessing and source-map
validation.

### Local Pilot Verdict

`qwen2.5-coder:7b` and `qwen2.5:7b` fail the semantic acceptance gate. Do not
scale either model to the complete concept corpus or Roam. The successful short
control does not outweigh fabricated source paths in a genuine note.

Ollama remains a viable provider boundary, but model qualification is mandatory.
The next local candidate must pass a fixed validation corpus before any vault
content is processed. Acceptance requires valid JSON, only allowlisted source
paths, no dangling endpoints, location coverage and repeatable results.

### Qwen 3 14B Qualification

MarkOS subsequently added a fail-closed, read-only validator for Graphify JSON.
It checks corpus-allowlisted paths, node and hyperedge identifier uniqueness,
edge and hyperedge endpoints, source-location coverage, confidence fields and
line-range bounds. Empty graphs are rejected. A reduced location threshold is
available only for diagnosis; the ingestion gate remains 100 percent.

The validator rejected the earlier `Active-Badge.md` graph for its fabricated
`src/auth/session.py` path, dangling endpoints and zero location coverage.

`qwen3:14b` was then tested through Ollama against the same short controlled
note. The run produced six nodes, five edges and one hyperedge from 885 input
and 1,539 output tokens in 137 seconds. The entities and endpoints were
structurally consistent, all source paths were allowlisted, and a diagnostic
validation with a zero-percent location threshold passed. Strict validation
failed because Graphify supplied no `source_location` values: coverage was zero
percent.

The 14B model was therefore stopped at the controlled-note gate. No genuine
note or larger corpus was processed with it. This result indicates that model
capacity alone does not solve the grounding gap: MarkOS-owned heading-aware
chunking and source mapping, or a Graphify extraction-contract change, is
required before further model qualification can pass.

### MarkOS Source Mapping Follow-up

MarkOS now prepares a deterministic external corpus of exact Markdown slices.
It preserves heading boundaries where possible and records every chunk's
original path, inclusive line range, source SHA-256 and chunk SHA-256 in a
portable manifest. A generated `.graphifyignore` prevents that manifest from
entering Graphify's evidence graph. Remapping fails if either hash changed or a
model cites a path absent from the chunk allowlist. Broad chunk-derived ranges
are labelled separately from provider-supplied chunk line locations.

The controlled note was repeated through this pipeline using `qwen3:14b`:

| Measure | Result |
|---|---:|
| Prepared chunks | 1 |
| Nodes | 6 |
| Edges | 5 |
| Hyperedges | 1 |
| Duration | 192 s |
| Input/output tokens | 946 / 2,091 |
| Original-path coverage | 100% |
| Source-location coverage | 100% |
| Strict validation | Pass |

The pipeline then advanced to the copied 654-word `Active-Badge.md` note. The
first run exposed Ollama's 4,096-token default context and was truncated. A
temporary model alias with an explicit 8,192-token context completed without
truncation in 296 seconds, using 1,868 input and 3,073 output tokens. It produced
nine relevant nodes, eight resolved edges and two resolved hyperedges, with no
fabricated corpus paths. Remapping achieved 100-percent original-path and
source-location coverage.

Strict validation nevertheless rejected the genuine-note graph because all
eight edges and both hyperedges omitted `confidence_score`, despite Graphify's
extraction specification making that field mandatory. MarkOS did not invent
the missing scores. The temporary 8K alias was removed after the test; the
underlying model remains installed. No larger corpus was processed.

### Constrained Confidence Normalization

Graphify's extraction specification fixes every explicitly `EXTRACTED`
relationship at `confidence_score: 1.0`. MarkOS now restores that single
deterministic value when the model supplies the `EXTRACTED` label but omits its
score. Each restored field is marked
`confidence_score_origin: markos_graphify_extracted_policy`. MarkOS still
rejects missing `INFERRED` and `AMBIGUOUS` scores because their values require
model judgement. Validation also enforces the score ranges associated with all
three labels.

The existing artifacts were reprocessed without another model call:

- the controlled graph again passed with six nodes, five edges, one hyperedge
  and 100-percent mapped location coverage;
- the genuine-note graph passed structural validation with nine nodes, eight
  edges, two hyperedges and 100-percent mapped location coverage; and
- ten missing scores in the genuine-note graph were restored from ten explicit
  `EXTRACTED` labels and carry the MarkOS policy-origin marker.

This is a structural and provenance pass, not yet full model qualification. All
19 genuine-note graph items cite the broad `L1-L74` chunk range, so location
precision, repeatability and human semantic review remain gating criteria. The
pilot was not scaled beyond this copied note.

### Reproducibility and Citation Precision

The copied `Active-Badge.md` note was prepared again with a 1,500-character
target. Heading-aware preparation produced four exact chunks covering `L1-L22`,
`L23-L49`, `L50-L65` and `L66-L74`.

An initial two-run trial retained full SHA-256 digests in model-facing chunk
paths. Both raw graphs were byte-for-byte identical, including the same
one-character corruption in one cited digest. Strict remapping rejected both.
This demonstrated reproducibility but also that long cryptographic identifiers
are unsuitable strings for a model to reproduce.

MarkOS now exposes short deterministic paths such as `chunks/0001/0004.md` and
keeps the complete source and chunk hashes only in the verified source-map
manifest. Two identical Qwen 3 14B runs then produced:

| Measure | Run 1 | Run 2 |
|---|---:|---:|
| Duration | 171.8 s | 165.9 s |
| Input/output tokens | 2,084 / 1,908 | 2,084 / 2,086 |
| Nodes | 9 | 10 |
| Edges | 8 | 8 |
| Hyperedges | 1 | 1 |
| Prepared chunks cited | 3/4 | 3/4 |
| Strict validation | Pass | Fail |

Run 1 passed and narrowed every accepted location to one of `L1-L22`,
`L23-L49` or `L50-L65`. Run 2 cited `L1-L22`, `L23-L49` and `L66-L74`, but
failed because an `INFERRED` hyperedge omitted its required score. The two raw
graphs had different SHA-256 digests. Using case-folded human labels, six of 13
unique node labels overlapped (46.2 percent Jaccard). Only three of 13 unique
label-relation-label edge signatures overlapped (23.1 percent Jaccard).

The finer corpus therefore improved citation breadth from 74 lines to at most
27 lines and eliminated path-transcription failure, but Qwen 3 14B did not meet
the reproducibility gate. Each run also omitted one different prepared chunk;
the union covered all four, while each individual run covered 75 percent.
MarkOS now reports cited and uncited chunk counts during remapping so source
coverage cannot be confused with graph-item provenance coverage.

### Claude Sonnet Qualification

The same four prepared Active Badge chunks were then tested twice through
Graphify's `claude-cli` backend with the `sonnet` model alias, one request at a
time. Claude Code 2.1.220 supplied JSON Schema constrained output, disabled
session persistence, and used the authenticated Claude Team subscription.
Nonessential Claude Code traffic was disabled for the trial.

| Measure | Run 1 | Run 2 |
|---|---:|---:|
| Input/output tokens | 34,200 / 6,834 | 34,200 / 6,351 |
| Nodes | 10 | 14 |
| Edges | 9 | 15 |
| Hyperedges | 1 | 3 |
| Prepared chunks cited | 4/4 | 4/4 |
| Source-location coverage | 100% | 100% |
| Strict validation | Fail | Fail |

Both runs kept source paths within the prepared-corpus allowlist. Hash-verified
remapping located all graph items in the original `Active-Badge.md` ranges, and
both runs cited every prepared chunk. Manual inspection found the entities
generally grounded in the note, including its explicitly listed papers and
applications.

Neither graph passed the confidence contract. Run 1 gave eight `EXTRACTED`
relationships probabilistic scores from 0.8 to 0.95 instead of the required
fixed score of 1.0. Run 2 did the same for fourteen relationships and also used
out-of-contract scores for one `AMBIGUOUS` and two `INFERRED` edges. MarkOS did
not overwrite provider-supplied scores.

The raw graphs differed. Using case-folded human labels, six of 18 unique node
labels overlapped (33.3 percent Jaccard). Four of 20 unique
label-relation-label edge signatures overlapped (20.0 percent Jaccard). Some of
this drift was granularity rather than fabrication—for example, one run used a
plain software label while the other qualified it—but it still makes graph
identity and updates unstable.

Claude therefore improves chunk coverage and grounding over the tested local
models, but the current Graphify extraction prompt and unconstrained semantic
identity do not meet MarkOS's validation or reproducibility gates. Do not scale
the `claude-cli` backend to the vault. The next provider experiment should use a
MarkOS-owned extraction contract with explicit conditional confidence rules,
canonical entity identity and a stability-oriented prompt, tested first through
the Anthropic API or a narrowly controlled Claude CLI adapter. Graphify can
remain a downstream graph consumer if that normalized contract qualifies.

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

1. Define a MarkOS-owned extraction contract with conditional confidence rules
   and canonical entity identity, then require repeated runs to meet an explicit
   node/edge stability threshold and both pass strict validation.
2. Define a prepared-chunk coverage policy and a bounded retry for chunks that
   produce no graph items; never silently treat omitted chunks as processed.
3. Perform human semantic review and measure entity duplication, runtime and graph
   reproducibility on the qualified model.
4. Verify add, change, rename and deletion behaviour using Graphify's supported
   incremental commands rather than only its structural cache.
5. Test a representative, copied Roam batch and measure graph size and query
   usefulness before attempting the complete legacy collection.
6. Define and test the `GraphProvider` contract against both Graphify and the
   existing DuckDB graph.

## Related

- [[Markdown Memory Architecture]]
- [[Milestones]]
- [[PARA Migration Strategy]]
- [[Mac App Architecture]]
