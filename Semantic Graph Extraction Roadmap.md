---
title: Semantic Graph Extraction Roadmap
type: roadmap
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-08-13
updated: 2026-08-13
reviewed: 2026-08-13
tags:
  - roadmap
  - architecture
  - knowledge-graph
  - provenance
---

# Semantic Graph Extraction Roadmap

This is the forward-looking specification: what the finished capability is for,
what it must never violate, and the sequence of phases to get there. It does
not narrate what has already been tried — that record is kept separately in
[[Semantic Graph Extraction]] and [[Graphify Evaluation]]. Prior experience
informed the constraints and phase ordering below, but this document should
read as something written before implementation, not after it.

## Purpose

A MarkOS user should be able to ask what their vault knows about a concept,
person, technology, or work — and where that knowledge came from — the same
way they can already search text or follow a wikilink. Today the vault holds
that knowledge only as prose scattered across notes. This initiative turns it
into a queryable graph of entities and relationships, surfaced through the
existing `markos graph` / `markos search` / `markos links` command surface, and
eventually consumed as one more evidence source by M13's concept-memory
research agent.

The graph is only valuable if it is trustworthy. A graph that occasionally
invents a connection is worse than no graph, because it looks authoritative. Every
phase below is constrained by that, before it is constrained by anything else.

## Constraints

These apply to every phase; a phase plan that requires violating one of these
is wrong, not the constraint.

1. **No embeddings, no vector database**, anywhere in this pipeline — MarkOS-
   wide rule, per [[Markdown Memory Architecture]].
2. **The vault is read-only.** This subsystem only ever reads Markdown; its
   outputs live in the external, disposable index or application-support
   storage, never written back into vault files.
3. **Zero-fabrication tolerance.** Every accepted entity and relationship must
   resolve to an exact quote at an exact, hash-verified location in a real
   source file. There is no partial credit for "plausible."
4. **A single generative-model call is never trusted on its own.** Its output
   is validated in code before acceptance, and its reproducibility must be
   measured, not assumed, before any phase depending on it is scaled up.
5. **Failure must be granular.** One bad claim rejects that claim, not the
   entire batch it arrived in. (This is a requirement the current contract does
   not yet meet — see Phase 2.)
6. **Prefer deterministic computation over a model call** wherever the source
   content is already structured enough to parse without one. A model call is
   the fallback for genuine semantic judgment, not the default tool.
7. **External LLM disclosure is a separate, explicit consent gate**, distinct
   from any vault-write permission, per [[Markdown Memory Architecture]].
8. **Cost is a real constraint, not an afterthought.** Per-call fixed overhead
   is large relative to small chunks; chunking strategy has direct cost
   consequences that must be considered alongside grounding precision.
9. **Human review stays in the loop.** Nothing in this initiative is
   authorized to treat its own output as settled fact without a person having
   looked at it, per [[Markdown Memory Architecture]]'s generated-note
   lifecycle.

## Relationship to Existing Milestones

This initiative sits **before and underneath** M13 in
[[MarkOS Specification]], not
alongside it. M13 describes an LLM research agent that searches, reads, and
synthesizes concept memory; this roadmap builds the semantic graph that agent
will eventually query as one more tool, the same way it already can query
backlinks or the link graph. None of the phases below require M13 to exist
first, and M13 does not require this initiative to be complete first — but the
integration point in Phase 8 assumes M13's tool-based research pattern.

## Phases

### Phase 1 — Deterministic Foundations

**Goal:** Extract everything the Markdown already states structurally, with no
model call and therefore no fabrication risk and no reproducibility question.

**Scope:**
- Parse bibliographic references into grounded `person`/`publication` entities
  (done — `markos.citations`).
- Fold resolved wikilinks into the same graph as first-class edges, reusing
  MarkOS's existing link-resolution index (`markos links`) rather than
  re-deriving link targets through any new mechanism.
- Extract frontmatter tags as grounded entities or entity attributes.
- Treat headings as candidate concept anchors within a note.

**Exit criteria:** A "deterministic-only" pass over a note produces zero
provider calls and captures at minimum citations, wikilinks, and frontmatter
tags as grounded, hash-verified graph items.

### Phase 2 — Grounded Model Extraction for the Remainder

**Goal:** Cover what Phase 1 cannot — genuine semantic judgment about concepts
and relationships expressed only in prose — without reopening the fabrication
question Phase 1 closed.

**Scope:**
- Send only the content Phase 1 could not resolve deterministically.
- Schema-constrained structured output; exact-quote grounding for every
  entity and relationship; conditional confidence scoring (`EXTRACTED` fixed
  at `1.0`, `INFERRED`/`AMBIGUOUS` required from the model or rejected).
- Only `EXTRACTED` relationships enter the stable graph; `INFERRED` and
  `AMBIGUOUS` are recorded as rejected, not silently dropped, pending a
  separate review workflow this roadmap does not yet define.
- **Required fix, not yet met:** an entity that fails grounding must be
  rejected individually, the same way relationships already are. A batch must
  never be discarded over one bad entity (Constraint 5).

**Exit criteria:** 100% grounding validation on real vault content, and a
demonstrated case where one deliberately-bad entity is rejected alone while
the rest of the batch is retained.

### Phase 3 — Reproducibility Threshold

**Goal:** Replace "reproducibility looks better than before" with an explicit,
agreed bar for "reproducible enough to scale past single-file testing."

**Scope:**
- Formalize a repeatable two-run comparison harness (node/edge overlap by
  union) as a standing check, not an ad hoc script.
- Decide the actual threshold — this is a judgment call for the project owner,
  not something the engineering work can resolve on its own.
- Evaluate self-consistency (multiple samples, keep only majority-agreed
  claims) as a candidate mechanism if single-run reproducibility does not
  clear the threshold.

**Exit criteria:** A documented, numeric reproducibility bar exists, a harness
enforces it automatically, and the current contract is measured against it —
pass or fail, either is an acceptable outcome of this phase, but "unmeasured"
is not.

### Phase 4 — Identity and Alias Resolution

**Goal:** The same real-world person or concept collapses to one node
regardless of which source form named them (a citation's `"Costanza, R."`
versus prose's `"Costanza"`, or a future second extraction source's own
naming choice).

**Scope:**
- Define an alias-resolution strategy (candidate: surname/initial matching
  with human confirmation for anything below a confidence bar; do not
  auto-merge silently).
- Apply it across sources within one note first, then across notes.

**Exit criteria:** A defined, tested merge rule exists and is applied before
node IDs are considered stable identities rather than per-mention artifacts.

### Phase 5 — Deterministic Relationships

**Goal:** Extend Phase 1's zero-model-call coverage from entities to
relationships, for the sources structured enough to support it.

**Scope:**
- Resolve wikilink edges between the linking note's own subject entity and the
  linked note's subject entity (the note-to-note link graph already exists;
  this phase turns it into typed graph edges).
- Decide whether and how citation relationships are worth adding — this
  requires defining both a new relation kind and a way to identify the
  "citing" entity, which nothing in the vault currently states explicitly.
  This may be judged not worth doing; that is an acceptable outcome, not a
  gap to force closed.

**Exit criteria:** Wikilink-derived edges appear in the graph with zero model
involvement. Citation relationships either ship with a defined contract or are
explicitly deferred with a stated reason.

### Phase 6 — Cross-Validation (exploratory)

**Goal:** Determine whether a second, independent, cost-free extraction pass
is worth running alongside the model pass as a confidence signal.

**Scope:**
- A local, non-generative model (evaluated candidate: GLiNER) runs on the same
  content; agreement between the two systems is a stronger signal than either
  alone, disagreement is surfaced for review rather than auto-resolved either
  way.
- This phase produces a decision, not necessarily an implementation — the
  prototype so far suggests genuine value but also real integration cost
  (alias resolution across systems, a second dependency, no relation
  extraction from the local model).

**Exit criteria:** A documented go/no-go decision, made after a scoped trial,
not a hunch.

### Phase 7 — Vault-Scale Rollout

**Goal:** Move from single-file testing to a real, bounded corpus before
considering the whole vault.

**Scope:**
- Process a complete existing folder (candidate: the `Localization Research`
  collection already used throughout testing) rather than one file at a time.
- Measure total cost, runtime, and cross-file entity behavior at that scale.
- Human review of a representative sample before any larger rollout is
  considered.

**Exit criteria:** One full folder processed, cost and runtime reported, no
fabricated cross-file entities found in the reviewed sample.

### Phase 8 — Integration with Concept Memory and Query

**Goal:** Make the graph something the user and M13's research agent can
actually use, not just something that exists.

**Scope:**
- Extend `markos graph` / `markos search` to surface semantic-graph entities
  and relationships alongside existing lexical and link results.
- Add a semantic-graph query tool to M13's research-agent tool set, alongside
  its existing search, read, wikilink, backlink, and graph-traversal tools.

**Exit criteria:** A person can ask a graph question through the existing CLI
surface and get an answer grounded in the same evidence rules as everything
else in this roadmap.

## Open Decisions

These need a human decision, not further engineering, before the relevant
phase can complete:

- What reproducibility number is actually "enough" (Phase 3)?
- Is silent-but-bounded alias auto-merging acceptable, or does every merge
  need human confirmation (Phase 4)?
- Is a citation-relationship contract worth defining at all, given no vault
  content currently states who is doing the citing (Phase 5)?
- Is a second local extraction pass worth its integration cost, given the
  prototype found real value but no free path to combining it cleanly with
  the primary contract (Phase 6)?

## Related

- [[Semantic Graph Extraction]] — the evaluation record this roadmap draws on.
- [[Graphify Evaluation]] — the earlier provider evaluation that motivated the
  MarkOS-owned contract this roadmap builds on.
- [[Markdown Memory Architecture]] — the provenance and disclosure rules every
  phase inherits.
- [[MarkOS Specification]] — M13 and M14, which this initiative feeds.
