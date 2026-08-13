---
title: MarkOS Specification
type: specification
status: draft
version: 2.1
owner: Mark Schulz
created: 2026-08-13
updated: 2026-08-13
reviewed: 2026-08-13
tags:
  - specification
  - architecture
  - roadmap
---

# MarkOS Specification

This is the document meant to be read before the first line of code is
written: what the system is, where knowledge lives, how it gets built up over
time, where and how AI is involved, what the finished system looks like, the
milestones that get there, and how to know at each stage whether it's
working. It incorporates and supersedes the former "Milestones",
"Mac App Architecture", and "PARA Migration Strategy" documents in full — all
three are retired in favor of this single document. [[Vision]] and
[[Markdown Memory Architecture]] remain separate: this document summarizes
their governing decisions but does not replace their detailed rationale.

## 1. Purpose and Philosophy

MarkOS is a personal knowledge engine that organizes, connects, and makes
accessible a person's own notes, documents, and research — independent of any
one editor, database, AI model, or company. Knowledge is meant to outlive the
tools used to create it.

Five principles govern every other decision in this document:

1. Knowledge is primary; software is not.
2. Markdown, in an ordinary folder on the user's own disk, is the canonical
   representation of that knowledge — never a database, and never a format
   only MarkOS can read.
3. The application layer is interchangeable. The same vault must remain fully
   usable in a plain text editor, in Obsidian, or with no software at all.
4. AI augments human judgment; it never replaces it. Every generated artifact
   is a proposal until a person approves it.
5. Small, composable components are preferred over one large integrated
   system — a rebuildable index, a filing service, a research agent, and a UI
   are separable pieces, not one monolith.

A corollary that shapes almost every later decision: **a system that
occasionally invents a fact is worse than a system with no AI at all**,
because a plausible-looking fabrication is harder to catch than an obvious
gap. Trustworthiness is evaluated before capability, throughout this
specification.

## 2. The Vault: Structure and Storage

The vault is a folder of Markdown files (and their attachments) that the user
already owns and controls, structured around the **PARA method** —
**P**rojects, **A**reas, **R**esources, **A**rchives — plus a capture inbox.

```text
Vault/
    00 Inbox/            newly captured or unclassified items awaiting filing
    10 Projects/         active work with a defined outcome and end date
    20 Areas/            ongoing responsibilities with no end date
    30 Resources/         reference material and topics of interest
    40 Archives/          completed or inactive material, kept for reference
```

Within any PARA destination, a **concept folder** is a named topic, project,
or area (`30 Resources/Heat Pumps/`, `10 Projects/Q3 Launch/`). As a concept
folder accumulates material beyond a single note, it grows an internal
structure of its own:

```text
30 Resources/Heat Pumps/
    raw/                 immutable original artifacts, byte-for-byte, hash-identified
    sources/             grounded Markdown notes, one per raw artifact, with cited evidence
    knowledge/           synthesized concept memory: current understanding, contradictions, open questions
        reviews/         periodic (e.g. weekly) generated knowledge and health review output
    Heat Pumps.md        the concept's own top-level note, if one exists directly
```

These three layers are strictly ordered, and each layer only cites the layer
below it, never generates content that outranks it:

```text
raw/ (immutable evidence)
    -> sources/ (grounded summary, cites raw/ by page or location)
        -> knowledge/ (synthesis, cites sources/ and human notes)
```

A concept folder does not need `raw/`, `sources/`, or `knowledge/` until it
needs them — a folder that only ever holds one human-written note stays that
simple. The structure is created incrementally, on demand, not scaffolded
up front for every topic.

This citation chain is vertical — each layer down to its own evidence.
Concept folders are also linked horizontally to each other: a `knowledge/`
note links out to other related concepts, and MarkOS proposes a matching
reciprocal link on the other end so the connection is discoverable from
either side, not just the direction it happened to be written in. See the
Backlink Strategy in Section 6 for the full mechanism, including how it
stays idempotent and never silently rewrites a human-authored link.

**Everything MarkOS itself needs that is not durable knowledge lives outside
the vault**, typically beneath a per-user application-support directory:

- the rebuildable lexical/link/graph index (disposable; deleting it and
  re-running discovery reconstructs it exactly);
- normalized intermediate representations of raw documents (e.g. extracted
  PDF text), which are a rebuildable cache of `raw/`, not a second source of
  truth;
- semantic-graph extraction working files (prepared chunk corpora, source
  maps) and any third-party semantic-graph tool's own operational output.

A vault with none of this external state is still a complete, self-contained
body of knowledge — nothing outside the vault is required to read it.

## 3. How Knowledge Is Built

Knowledge accumulates through a pipeline, not a single action. Every stage
below is a proposal until a human confirms it; nothing here writes to the
vault silently.

```text
capture -> classify & file -> ingest raw evidence -> generate grounded source notes
    -> synthesize concept memory -> maintain and periodically review
```

1. **Capture.** A new document or note enters through the inbox. Nothing is
   classified yet.
2. **Classify and file.** The system proposes a PARA category and a concept
   folder — preferring an existing folder over creating a new one — and shows
   the exact move before applying it. Filing is mechanical file movement, not
   content generation.
3. **Ingest raw evidence.** A raw document (PDF first) is identified by
   content type and hash, and its original bytes are preserved untouched
   beneath the concept folder's `raw/`. Text is extracted locally
   (with OCR as a fallback for scans) into a rebuildable, out-of-vault
   representation — the extraction is never treated as the source itself.
4. **Generate grounded source notes.** A source note is created under
   `sources/`, summarizing one raw artifact with claims linked to specific
   pages or locations in it. It is marked machine-generated and unreviewed
   until a person reviews it.
5. **Synthesize concept memory.** A research process reads across existing
   `sources/`, `knowledge/`, and human-written notes — by searching, reading,
   and following links and the resolved graph, never by loading the whole
   vault at once — and proposes `knowledge/` Markdown that records current
   understanding, contradictions, and open questions. Every factual sentence
   in it traces back to a source or human note. It also proposes outgoing
   links to other related concept notes it drew on, and a matching reciprocal
   link on each of those notes in return, so the connection is discoverable
   from either side.
6. **Maintain.** Health checks and periodic (e.g. weekly) reviews detect
   staleness — a source whose underlying hash changed invalidates everything
   generated from it — and propose repairs or knowledge updates, never
   applying them without approval.

**Worked example.** A research PDF is dropped into `00 Inbox/`.

- MarkOS proposes: `30 Resources/Heat Pumps/`, an existing folder, over
  creating a new one.
- On approval, the PDF moves to `30 Resources/Heat Pumps/raw/study.pdf`,
  identified by its SHA-256.
- Page-aware text is extracted locally and cached outside the vault.
- A source note `sources/Heat Pump Efficiency Study.md` is generated, citing
  specific pages for its claims, marked unreviewed.
- The next scheduled synthesis pass reads this new source alongside existing
  Heat Pump material and proposes an update to `knowledge/Heat Pumps.md` — a
  reviewable diff, not a silent rewrite — recording what's newly confirmed,
  what contradicts prior notes, and what remains an open question.
- If the study is later replaced with a corrected version, its changed hash
  invalidates the source note and everything synthesized from it, and both
  are flagged stale rather than silently left looking current.

A **semantic graph** — entities and relationships extracted from note text
itself (people, concepts, technologies, works, and how they relate) — is a
parallel, structural view over the same evidence, feeding search and
synthesis as an additional tool rather than a separate pipeline. It is held
to the same standard as everything else in this section: every entity and
relationship in it must trace to an exact quote at an exact location in a
real file, or it does not appear in the accepted graph at all.

## 4. Where and How AI Is Used

AI is a tool invoked at specific, narrow points — never a general agent with
standing access to modify the vault.

| Stage | What the model sees | What it produces | Deterministic alternative used first |
|---|---|---|---|
| Raw document summarization | Extracted text of one raw artifact | A grounded source note with page-cited claims | Local, page-aware extraction (no model) |
| Concept-memory synthesis | Search results, note contents, and graph traversals it explicitly requested | Proposed `knowledge/` Markdown with source citations | Lexical search, wikilinks, backlinks, resolved graph — all without embeddings |
| Semantic-graph extraction | One prepared, hash-verified chunk of a note at a time | Schema-constrained entities and relationships, each with an exact evidence quote | Structural content (citations, links, tags) is parsed in code first; a model only sees what's left |
| Periodic knowledge/health review | Deterministic graph deltas and change reports | Proposed review notes and research questions | The deltas themselves are computed without a model |
| Interactive research query | Search results, note contents, and graph traversals it explicitly requested, scoped to whatever the question covers | A synthesized answer with inline citations and a visible research trace | Same search/read/link/graph tools as concept-memory synthesis — this is the on-demand form of the same mechanism |

### Interactive Research Queries

Concept-memory synthesis (Section 3, stage 5) does not have to wait for a
scheduled batch. The same research mechanism — search, read, follow links,
collect evidence, answer only from what was found — is also available on
demand, asked directly through the UI.

**What a question looks like:**

- *"What have I learned about heat pump efficiency in cold climates?"* — a
  broad, single-concept question, answered by synthesizing across every
  relevant `sources/` and `knowledge/` note.
- *"Does anything in my notes contradict the manufacturer's quoted COP for
  the Daikin unit?"* — a contradiction-focused question, where the point of
  the answer is surfacing disagreement, not resolving it silently.
- *"In my Q3 Launch project, what have we decided about vendor selection?"*
  — a question explicitly scoped to one active project.
- *"What other projects or resources mention heat pumps?"* — a discovery
  question, answered largely from the resolved link graph and semantic graph
  rather than free-text search.

**What a response looks like:** a short synthesized answer, every factual
sentence carrying an inline citation to the specific note (and page or
location, if the source is a raw document) it came from — for example,
*"Efficiency drops significantly below −15°C [Heat Pump Efficiency Study,
p.12]; however, your field notes from the cabin retrofit recorded stable
output at −20°C [Cabin Retrofit Notes], which hasn't been reconciled with the
study."* Alongside the answer, a visible research trace lists exactly which
notes were searched, read, and cited (satisfying the same transparency
requirement as scheduled synthesis), and the answer can optionally be saved
as a `knowledge/` note through the same preview-and-confirm flow as any other
generated content — an ephemeral answer becomes durable memory only on
request, never automatically.

**What range of folders it searches, and how that's constrained:** by
default, the whole vault — every PARA category, including `Archives/` and
anything still sitting unfiled in the inbox. Restricting scope by default
would quietly hide the cross-topic connections a personal knowledge tool
exists to surface, and the underlying search/link/graph tools are cheap
enough at any vault size that there's no performance reason to narrow it
either. Instead, every citation is labeled with the PARA category and
review status of the note it came from, so the user judges relevance and
currency themselves — an old, archived note showing up in an answer is
informative, not a leak, as long as it's visibly labeled as archived. A user
who wants a faster or more precise answer can explicitly narrow the question
to one PARA category, one concept folder, or a specific set of folders; this
is a plain filter applied to the same underlying tools, surfaced in the UI as
a scope selector next to the question box (defaulting to "Entire vault"),
not a separate search mechanism with different rules.

Rules that apply everywhere AI appears:

- **External disclosure is a separate permission from vault-write
  permission.** Approving one never implies the other.
- **Local and external providers share the same boundary**, and that boundary
  is decided per stage, not once for the whole system. The semantic-graph
  stage's current external provider, for example, is Claude Code's own CLI,
  invoked per chunk with tool use and session persistence disabled — the model
  gets exactly the text it needs and nothing else, and cannot take any action
  beyond returning structured output. Raw-document summarization and
  concept-memory synthesis have not been implemented yet and have no provider
  decision of their own; they are expected to follow the same boundary, not
  assumed to share this specific choice.
- **Document and note content is untrusted data passed to the model, never
  an instruction to it.** A note's text cannot direct the system to do
  anything; it can only be described, summarized, or have entities extracted
  from it.
- **Model output is validated locally before acceptance**, against a fixed
  schema, exact-quote grounding, and confidence rules — not trusted because it
  parsed as JSON.
- **Credentials never appear in vault files, logs, or generated notes.**
- **Provider and model identity are recorded on generated content** (without
  credentials), so a person can see what produced a given claim.

What never involves AI, by design: discovery, parsing, indexing, freshness
checking, the mechanical act of filing an approved move, lexical search, link
resolution, graph traversal, and the public read-only HTTP API. These form
the deterministic foundation everything else — including every AI-touched
stage above — depends on, and they remain fully useful with no AI provider
configured at all.

## 5. The Final System

The finished system is not one application but a small set of interfaces
over the same underlying services, so that no interface can apply a different
safety rule than another.

```text
                    vault, index, and optional LLM providers
                                    |
                    shared application services
   (propose -> validate -> preview -> confirm -> apply -> audit -> synchronize)
                    /                          \
              MarkOS CLI                  PySide6 desktop app
                                        (same Python process,
                                         no separate helper)
                                    |
                          read-only HTTP API
                     (for other tools and integrations)
```

### Shared Application Services

CLI and UI code must not duplicate business rules, or invoke and parse each
other's user-facing commands. Every workflow is modeled as a typed service
following one fixed pipeline — `propose -> validate -> preview -> confirm ->
apply -> audit -> synchronize` — with stable domain models as inputs and
outputs, not console text or Qt widgets. This is what lets the CLI, the
desktop app, and automated tests exercise exactly the same safeguards, and
lets the service layer be tested with no UI toolkit imported at all.

### Interfaces

- **The CLI is permanently first-class**, not a stepping stone to the GUI. A
  user with no interest in a graphical app can run MarkOS entirely from the
  command line, indefinitely.
- **The desktop app** is built with PySide6 (Qt for Python), running in the
  same Python process as the CLI's application services — no bundled helper
  process, no inter-process protocol to version or secure. It is the primary
  day-to-day interface for most users, providing:
  - a dashboard for index, watcher, vault and health status;
  - an inbox for Markdown and raw documents;
  - PARA and concept-folder proposals with full move-plan diffs;
  - concept-link and reciprocal-backlink review;
  - raw extraction and grounded source-note previews;
  - `knowledge/` notes, contradictions, and research suggestions;
  - an interactive research-query view (Section 4) for on-demand, cited
    questions, scoped to the whole vault or a chosen subset;
  - weekly knowledge and health review queues;
  - audit history and undo;
  - local and external LLM provider settings;
  - separate, explicit controls for vault-write permission and external LLM
    disclosure; and
  - menu-bar status and review notifications.

  It remains genuinely useful with no LLM configured at all — search, links,
  graph, and health stay available.
- **The read-only HTTP API** lets other tools query the index without ever
  being able to mutate the vault through it.
- **Optional background operation** — a user-registered, reversible per-user
  `launchd` LaunchAgent, not a root daemon — runs freshness checks daily,
  health/knowledge review weekly, and a deep-integrity check monthly,
  surfacing results for review. It never applies anything on its own, and can
  be paused, resumed, or unregistered from the app at any time.

### Vault Access

The user selects a vault through a native folder picker. The app is not
sandboxed, so it persists the selected path directly rather than through a
security-scoped bookmark — the App Sandbox mechanism that bookmark exists for
is only required for Mac App Store distribution, which is deferred (see
Packaging, below). Ordinary macOS per-app folder-access consent still
applies and is unaffected by this choice. MarkOS applies a narrower policy on
top of whatever the OS grants:

- reads are allowed for configured discovery and research;
- writes are disabled by default;
- approved writes are restricted to configured inbox and PARA paths;
- source hashes are checked before applying changes;
- collisions and symlink escapes are rejected; and
- external LLM disclosure remains a separate permission.

Credentials are stored in macOS Keychain via the cross-platform `keyring`
package, never in repository or vault files.

### Packaging and Distribution

The app is packaged as a standard `.app` bundle (via PyInstaller or a
comparable tool) containing the Python runtime, compiled dependencies
including DuckDB, and application code. Application and index-schema
versions are checked explicitly at startup.

Initial development uses local builds. The first public distribution target
is a Developer ID-signed and notarized direct download, using Apple's
command-line `codesign` and `notarytool` — full Xcode is not required, since
there is no SwiftUI project to build. Mac App Store distribution is deferred
until sandbox constraints are evaluated for a PySide6 app specifically.

### Alternatives Considered for the Desktop App

- **Native SwiftUI client.** The original design used SwiftUI, controlling
  the Python core through a bundled helper process and an authenticated
  local IPC protocol. This gives the fullest native polish available on
  macOS — Apple HIG animations, Dynamic Type, native dark-mode transitions,
  and the deepest OS integration (widgets, Shortcuts, Spotlight) — at the
  cost of a second language and toolchain, a full Xcode dependency, and an
  entire helper-process/IPC-protocol layer (startup, shutdown, crash
  recovery, version compatibility) that a single-process design does not
  need at all. Reconsidered in favor of PySide6: for a personal, single-user
  tool, the native polish did not outweigh maintaining two languages and an
  IPC boundary. Revisitable if native OS integration becomes a priority the
  Python stack cannot meet.
- **Tauri with a Python sidecar.** Adds Rust, Node, and web-frontend
  technology without a current cross-platform requirement. Not justified for
  the first client.
- **Browser UI over the FastAPI surface.** Could validate information
  architecture quickly, but does not provide the native permission,
  menu-bar, Keychain, scheduling, and distribution experience PySide6
  provides directly. Usable only as a disposable prototype.

### Security Boundaries

- The UI and application services run in one process; there is no separate
  helper or IPC channel to secure.
- Mutating methods are not added to the public read-only API.
- The UI cannot bypass application-service validation.
- Model-generated paths, claims, and references are treated as untrusted.
- Scheduled operations generate proposals; they do not approve them.
- Application crashes and partial operations preserve the original vault and
  valid index.

### A Day in the Life

A user's daily experience, once the system is complete: new material lands in
an inbox and gets filed with one approval; documents are automatically
summarized with page-level citations; a question can be asked at any time and
answered with cited evidence from as much or as little of the vault as the
user chooses; once a week, a review queue surfaces what's newly connected,
what contradicts what, and what's gone stale — each as a diff to accept,
edit, or reject; and at any time, search, links, and the knowledge graph are
queryable instantly, offline, without waiting on a model.

## 6. Implementation Milestones

Milestones are fixed in scope and delivered as small, releasable increments.
Later milestones must not bypass safeguards established by earlier ones.

### Delivered

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

### M10 — Automated Read-Only Synchronization

Status: **Complete — implemented in `markos` commit `c60400d`**

**Goal.** Keep the external DuckDB index current automatically while
preserving Markdown as the read-only source of truth.

**Scope:**
- Add a foreground `markos watch` command.
- Detect added, modified and deleted Markdown files.
- Debounce bursts of filesystem activity into a single synchronization.
- Invoke the existing conditional and transactional synchronization boundary.
- Prevent concurrent index rebuilds.
- Continue monitoring after recoverable discovery or indexing errors.
- Support clean shutdown and structured operational logging.
- Preserve the requirement that the index resides outside the Markdown vault.

**Non-goals:** writing to or reorganizing the Markdown vault; installing or
managing a background operating-system service; incremental mutation of
individual DuckDB records; adding write operations to the HTTP API; building
a graphical user interface.

**Design constraints:** Markdown remains canonical and read-only to MarkOS;
DuckDB remains an external, disposable and fully rebuildable index; M10
composes the M8 freshness and M9 synchronization capabilities rather than
introducing a second indexing path; watch behavior must be testable without
depending on real-time sleeps or a user-owned vault.

**Acceptance criteria:**
1. Adding, modifying or deleting Markdown automatically results in a current index.
2. Multiple rapid changes are coalesced into one synchronization attempt.
3. When no source changes exist, the index remains byte-for-byte unchanged.
4. Interrupted or failed rebuilds preserve the previous valid index.
5. Concurrent watcher or rebuild activity cannot corrupt the index.
6. Recoverable errors are logged and do not terminate the watcher.
7. The watcher shuts down cleanly without leaving temporary index files or locks.
8. Automated tests use temporary vaults and deterministic timing.
9. Ruff, strict mypy and the complete test suite pass.

**Completion.** Implemented and verified on 2026-08-12: foreground watcher,
debounced change handling, retry behavior, clean shutdown, and cross-process
rebuild serialization without persistent lock files.

### Migrating Existing Content (pre-M11)

Existing vault collections are assessed in place with a read-only migration
audit before controlled writes are enabled anywhere. The inbox is reserved
for newly captured or genuinely unclassified items; it is not a staging
destination for large legacy collections. The audit produces evidence and
proposals, not file changes — a later migration planner generates complete
move, folder-creation and managed-link diffs for explicit approval, and
actual writes belong to M11's controlled write boundary below.

**Audit tool.** The pre-M11 `markos para-audit` command inventories selected
vault-relative folders using read-only filesystem and DuckDB graph queries.
It reports: Markdown and non-Markdown file counts and total size; existing
`raw/`, `sources/` and `knowledge/` structure; nested Git repositories;
outgoing, unresolved, internal and external inbound links; a candidate PARA
destination; migration risk and recommended migration strategy; and a
preview of notes whose links may be affected. Reports may be printed or
saved outside the vault; MarkOS rejects report paths inside the vault.

**Initial audit findings** (run 2026-08-12 against a current index; no vault
files or folders were modified):

| Source | Markdown | Non-Markdown | External inbound links | Initial treatment |
|---|---:|---:|---:|---|
| Localization Research | 48 | 288 | 0 | Adopt as `Resources/Localization Research` |
| Remote Laboratories | 0 | 1 | 0 | Pilot `Resources/Remote Laboratories` |
| Synthetic Biology | 1 | 1 | 0 | Pilot `Resources/Synthetic Biology` |
| Roam | 3,427 | 180 | 6,332 | Batch migration in place to multiple PARA destinations |

- *Localization Research* already contains `raw/` and `knowledge/`, 84 PDFs,
  operational outputs, and a nested Git repository — it resembles the
  desired concept-folder layout and should be adopted and normalized rather
  than moved through the inbox. Before migrating it, decide whether the
  nested repository and generated caches, graphs, manifests and outputs
  belong inside the Obsidian vault at all; the migration planner must
  distinguish source knowledge from rebuildable operational artifacts.
- *Remote Laboratories* is a low-risk pilot with one PDF under `raw/`: move
  the concept folder under `Resources/`, generate a grounded source note
  under `sources/`, and create `knowledge/` only once cross-source synthesis
  exists.
- *Synthetic Biology* is a low-risk pilot with a PDF and one Markdown file
  under `raw/`; review whether that Markdown file is extracted/source
  material before placing it under `sources/`, since raw folders normally
  hold only immutable original artifacts.
- *Roam* is a large legacy export — thousands of notes, unresolved links, and
  extensive inbound references from the rest of the vault. Moving it
  wholesale into the inbox would obscure its existing provenance and
  structure, generate unnecessary path churn, overload the new-item review
  workflow, make link repair harder to reason about, and force one false
  PARA classification onto what is really a multi-topic collection. It stays
  in place while MarkOS audits and migrates reviewed batches to Projects,
  Areas, Resources, or Archives; each batch must include a move plan,
  reciprocal-link plan, ambiguity report, and rollback record.

**Migration order**, each pilot completing preview, hash validation, apply,
index rebuild, link health check, and rollback verification before the next
class of migration begins:

1. Remote Laboratories, as the smallest raw-document pilot.
2. Synthetic Biology, as a raw-plus-Markdown pilot.
3. Localization Research, as an existing structured concept adoption.
4. Roam, as a separate, incremental legacy-migration program.

**Backlink strategy.** Moving files can affect explicit path links even when
ordinary Obsidian backlinks remain discoverable. Migration planning therefore
separates: virtual backlinks already available from the index; outgoing
concept links proposed for moved or generated notes; reciprocal links
proposed for existing notes (this is the mechanism referenced in Section 2);
and human-controlled links that are reported but never silently rewritten.
Managed reciprocal sections are idempotent and authorship-neutral — human-
and AI-authored notes receive the same concept-link analysis, but MarkOS
edits only explicitly managed regions after approval.

### M11 — Controlled PARA Filing

Status: **Accepted — implementation not started**

**Goal.** Introduce an explicit, default-deny vault-write boundary that can
file notes from a configured in-vault inbox into human-approved PARA
destinations.

**Scope:**
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

**Non-goals:** editing note contents; moving arbitrary existing notes outside
the inbox; silent autonomous filing; raw document extraction or
summarization; installing a background write service.

**Acceptance criteria:**
1. Vault writes are disabled unless explicitly configured.
2. Suggestion and preview commands never modify the vault.
3. Only files currently inside the configured inbox can be filed.
4. Destinations remain within configured PARA roots and no existing file is overwritten.
5. A changed source hash invalidates the proposal before any write occurs.
6. Folder creation and moves are recoverable, audited and undoable.
7. A failed filing operation preserves the original inbox item.
8. The external index is current after a successful filing operation.
9. Tests use temporary vaults and verify files outside the allowed paths remain unchanged.

### M12 — Raw Document Ingestion and Grounded Source Notes

Status: **Accepted — implementation not started**

**Goal.** Ingest raw documents from the vault inbox while preserving the
original artifact and creating a source-grounded Markdown summary suitable
for later research.

**Scope:**
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

**Non-goals:** treating an LLM summary as primary evidence; modifying or
optimizing the original artifact; automatically accepting generated claims
without evidence; cross-source concept synthesis; embeddings or vector
search.

**Acceptance criteria:**
1. The stored raw artifact is byte-for-byte identical to the inbox artifact.
2. Source notes link to the raw artifact and cite page-aware evidence for material claims.
3. External disclosure is disabled by default and separately approved from vault writes.
4. API credentials never appear in repository configuration, logs or generated notes.
5. Duplicate source hashes do not create duplicate raw artifacts without approval.
6. Failed extraction, summarization or filing leaves the inbox artifact recoverable.
7. Low-quality extraction is reported rather than hidden behind an overconfident summary.
8. Changing a raw source hash invalidates all derived extraction and source-note state.
9. The generated source note is clearly marked machine-generated and unreviewed.

### M13 — Filesystem-Native Research and Markdown Concept Memory

Status: **Accepted — implementation not started**

**Goal.** Allow an LLM to research the Markdown vault through explicit
lexical and graph tools, then write provenance-preserving Markdown as durable
concept memory, without embeddings or a vector database.

**Scope:**
- Provide narrow tools for text, title and metadata search; note reading; wikilinks;
  backlinks; and graph traversal.
- Let the research agent iteratively search, inspect promising notes, follow links and
  refine queries instead of loading the entire vault into one context window.
- Use DuckDB only as an optional rebuildable accelerator for lexical and graph queries.
- Generate concept-memory Markdown that synthesizes grounded source notes and human notes.
- Store generated synthesis under each concept folder's `knowledge/` directory.
- Link factual conclusions to source or human notes and ultimately to original evidence.
- Distinguish source-supported facts, cross-source synthesis, model inference and contradiction.
- Mark generated memory with source hashes, generation status and review status.
- Detect stale memories when their recorded sources change or new relevant sources appear.
- Preview new memories and refreshes as reviewable Markdown diffs before writing.
- Support both local and explicitly approved external LLM providers.
- Provide the same research mechanism on demand as an interactive query
  (Section 4), not only as scheduled batch synthesis.

**Non-goals:** embedding generation, semantic vector search or a vector
database; treating generated memories as independent evidence; silently
overwriting human-authored notes; autonomous publication of generated
conclusions as reviewed knowledge; loading the entire vault into every model
request.

**Acceptance criteria:**
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
11. Interactive queries answer from the same evidence and citation rules as
    scheduled synthesis.

### M14 — Knowledge Maintenance and Health

Status: **Accepted — implementation not started**

**Goal.** Maintain trustworthy indexes, links, provenance and generated
knowledge through deterministic health checks and periodic, reviewable
knowledge suggestions.

**Scope:**
- Add read-only index, link, provenance, folder and generated-memory health checks.
- Verify every discoverable Markdown file is represented by a current source fingerprint.
- Report rejected files, stale index rows, missing managed-link targets and ambiguous aliases.
- Verify source notes point to existing raw artifacts whose hashes still match.
- Verify generated knowledge resolves to source or human evidence rather than generated memory alone.
- Detect stale knowledge when source hashes change or relevant sources are added.
- Calculate deterministic graph deltas and relationship changes since the previous review.
- Generate repair proposals separately from read-only health reports.
- Generate weekly knowledge-review and research-question proposals only when meaningful changes exist.
- Store periodic synthesis under the relevant concept folder's `knowledge/reviews/` directory.
- Expose deterministic non-interactive commands suitable for external scheduling.

**Non-goals:** silent repair of vault files; real-time LLM synthesis after
every edit; treating a note with no backlinks as invalid; embedding an
operating-system scheduler in the Python core.

**Acceptance criteria:**
1. Health checks never modify the vault or index.
2. Index health detects missing, stale, rejected and incompatible state.
3. Link health distinguishes unresolved, ambiguous, isolated and broken managed links.
4. Provenance health detects missing raw artifacts, changed hashes and invalid evidence references.
5. Repair and knowledge proposals are preview-only until explicitly confirmed.
6. A weekly review is omitted when no meaningful knowledge change exists.
7. LLM output is validated against deterministic graph facts and evidence identifiers.
8. Scheduled commands are idempotent and emit machine-readable status.
9. Daily, weekly and monthly cadences are configurable rather than hard-coded.

### M15 — Desktop App

Status: **Accepted — implementation not started**

**Goal.** Provide the native-feeling desktop interface described in Section
5, built with PySide6, for the same safe operations available through the
MarkOS CLI.

**Scope:**
- Refactor new workflows into UI-neutral Python application services shared by CLI and app.
- Call those application services directly from the same process — no
  bundled helper or inter-process protocol.
- Provide dashboard, inbox, PARA proposal, multi-file diff, link review,
  health, audit and interactive-query views.
- Provide source-summary, concept-memory and weekly-review approval workflows.
- Provide LLM provider, disclosure, vault-write and scheduling settings.
- Select the vault through a native folder picker and persist the selected path directly.
- Store credentials in macOS Keychain (via the `keyring` package) rather than configuration files.
- Provide menu-bar visibility for watcher state and pending reviews.
- Keep the existing public FastAPI interface read-only.

**Non-goals:** splitting business logic between the UI process and a
separate service process; making CLI and UI implementations follow different
business rules; exposing mutating vault operations through the public HTTP
API; App Store distribution in the first release; automatic application of
proposed writes or external disclosure.

**Acceptance criteria:**
1. CLI and UI invoke the same Python application-service contracts.
2. The app cannot bypass hash, path, collision, approval or audit safeguards.
3. Vault access is limited to the user-selected folder and persists across launches.
4. Every multi-file mutation is displayed as a reviewable plan or diff before approval.
5. External LLM disclosure is separately visible and controlled.
6. Application startup, shutdown, and crash recovery preserve the vault and valid index.
7. The app remains useful with no LLM provider configured.
8. Automated service-contract tests run independently of the PySide6 UI layer.

### M16 — Background Operations and Distribution

Status: **Accepted — implementation not started**

**Goal.** Deliver user-approved background maintenance, notifications and a
trustworthy installation and update path for the desktop app.

**Scope:**
- Register an optional per-user LaunchAgent through a standard `launchd` property list.
- Provide daily freshness, weekly health/knowledge and monthly deep-integrity schedules.
- Notify the user when reviewable reports or failures require attention.
- Allow background work to be paused, resumed and unregistered from the app.
- Package the Python runtime and compiled dependencies inside the app bundle.
- Sign with Developer ID, submit for notarization and staple the notarization ticket.
- Define version compatibility between the application and the index schema.
- Provide a tested direct-download installation and update strategy.

**Non-goals:** root launch daemons; background vault repairs without
approval; mandatory always-on operation; Mac App Store distribution until
sandbox constraints are proven.

**Acceptance criteria:**
1. Background registration is explicit, reversible and visible in macOS settings.
2. Scheduled failures preserve existing index and vault content and notify the user.
3. No scheduled job applies repair, filing or generated-memory proposals silently.
4. The app bundle contains all required runtime components.
5. Release builds pass code-signing, Gatekeeper and notarization verification.
6. Application and index-schema version mismatches fail safely with actionable guidance.
7. Clean installation, upgrade, rollback and removal are documented and tested.

### Sequencing

The read-only PARA migration audit, above, prepares the existing vault
without enabling writes. M11 establishes the safe write boundary. M12 builds
raw-source provenance on that boundary. M13 consumes grounded Markdown from
M12 and the existing vault to create concept memories. M14 maintains their
integrity and periodic review. M15 exposes the shared services through the
desktop app. M16 adds user-approved background operation and distribution.
Later milestones must not bypass safeguards established by earlier ones.

### Recording Progress

Design-level progress — decisions accepted, evaluations run, milestones
accepted or completed — is recorded in `markos-design/CHANGELOG.md`,
organized by design milestone rather than software release, under
*Accepted* / *Completed* / *Evaluated* headings as it happens. It is the
running log that this specification's own milestone **Status** fields
summarize as a point-in-time snapshot, not a duplicate of it.

Implementation-level progress — actual code shipped — lives in the `markos`
repository's own git commit history. Once a milestone is implemented, its
entry in this document is updated in place: **Status** changes from
*Accepted* to *Complete*, and a short **Completion** note is added citing
the specific commit, exactly as M10 already does above. This specification
is not a second changelog; it always reflects the current state, while
`CHANGELOG.md` and `markos`'s git history preserve how it got there.

## 7. Agents and Sub-Agents

Two distinct questions: where do multiple AI agents help *build* MarkOS, and
where might MarkOS's own *running* system use more than one agent to do its
work? These are different concerns with different constraints, covered
separately below.

### Building MarkOS: Agentic Implementation

Each milestone in Section 6 is deliberately scoped as a small, independently
verifiable unit of work — this is what makes it suitable for agentic
implementation, coding-agent-assisted or otherwise:

- **One milestone, one focused implementation pass.** A milestone's Goal,
  Scope, Non-Goals, and Acceptance Criteria together form a complete brief:
  an agent implementing M11 needs nothing beyond that milestone's own section
  to know what "done" means, and the numbered acceptance criteria are
  directly checkable, not just descriptive.
- **Independent verification before considering a milestone complete.** A
  second, independent agent pass — reviewing the first agent's
  implementation against the milestone's own acceptance criteria, not
  re-deriving new criteria — catches the gap between "the code runs" and
  "the code satisfies the constraints in Section 1." This mirrors the
  project's own rule that model output is validated, not trusted (Section 4),
  applied to the build process itself.
- **Isolated exploration before committing to an approach.** Evaluating a
  candidate library, model, or technique (the kind of comparison recorded in
  `CHANGELOG.md`'s *Evaluated* entries) is scoped work suited to a disposable,
  isolated environment — a prototype that never touches the real project's
  dependencies or a real vault, and is discarded or promoted based on its
  findings, not left half-integrated.
- **Fan-out for genuinely independent, parallel work.** Within one milestone,
  independent sub-tasks — writing tests against already-agreed acceptance
  criteria, drafting user-facing documentation, auditing an unrelated part of
  the codebase for the same class of bug — can run as parallel agent work
  without needing to share state, so long as they don't touch the same
  files. Sequencing across milestones (Section 6) remains strict;
  parallelism is a within-milestone tool, not a way to skip the dependency
  order.
- **The vault itself is never a build artifact.** Agents implementing MarkOS
  operate on the `markos` and `markos-design` repositories. They must never
  be given standing access to a real user vault as part of development or
  testing — test vaults are synthetic and disposable, matching the same rule
  Section 4 applies to MarkOS's own runtime.

### Version Control and Pull Requests

Two separate repositories, two different weights of process, matching what
each actually needs.

**`markos` (the implementation).** Every milestone — or a well-scoped slice
of one, when a milestone is large enough to split — is developed on its own
branch, prefixed `codex/`, and opened as a pull request before merging to
`main`; nothing is committed directly to `main`. A pull request:

- describes what changed and why, in the same terms as the milestone's own
  Goal;
- states which of the milestone's numbered acceptance criteria it satisfies,
  and how they were checked — test output, a manual verification note, or
  both;
- must pass the same gate defined in Section 6 (`ruff check`, `mypy`,
  `pytest`, and a lock-file check) before it is considered mergeable — run
  locally today, with automatically running that same gate as a CI check on
  every pull request as the natural next step, rather than trusting a local
  run alone;
- is merged with a merge commit, not squashed — the project's convention of
  small, single-purpose commits (`feat:`, `fix:`, `docs:`, `chore:` prefixes)
  is itself the readable history, and squashing would throw that away;
- has its branch deleted, both locally and on the remote, once merged — a
  merged branch carries no information the merge commit and the pull
  request's own record don't already preserve.

**Review**, today, means an independent verification pass before merging, not
after — either a second agent checking the first's work against the
milestone's acceptance criteria (the Building MarkOS practice above, applied
here) or the project owner's own review. This is the same "generated output
is validated before being trusted" rule the rest of this specification
applies everywhere else, applied here to code changes instead of extracted
facts. If other people ever contribute, this becomes an explicit
reviewer requirement rather than a self-review discipline; nothing about the
branch/PR/CI structure above needs to change to get there.

**`markos-design` (this document and its neighbors).** Design documents are
reviewed before being committed, per this repository's own stated principle,
but commit directly to `main` — there is no CI to gate on and no acceptance
criteria to check mechanically, so the heavier PR process above would add
process without adding safety. A change that reverses an already-accepted
decision — as this document itself has done more than once — is still
expected to be reviewed deliberately; it just doesn't need a pull request to
enforce that.

### Running MarkOS: Agentic Execution

Most of MarkOS's own AI usage (Section 4) is a single agent iterating with
tools — search, read, follow links, synthesize. A few specific points in the
pipeline have a natural fan-out/fan-in shape, where dividing the work across
sub-agents is a genuine architectural option, not just a performance tweak:

- **Semantic-graph extraction, per chunk.** Section 4 already dispatches one
  prepared chunk per model call. Nothing about the grounding contract
  requires those calls to be sequential — independent chunks can be
  processed concurrently by parallel sub-agent calls, with the existing
  deduplication and canonicalization step (Section 3) serving as the merge,
  exactly as it already does for sequential calls. This only ever changes
  wall-clock time, never the grounding guarantee.
- **Broad interactive queries, per concept folder.** A question spanning many
  concept folders (Section 4's *"what other projects or resources mention
  heat pumps?"* example) can fan out one sub-agent search per candidate
  folder or PARA category, each returning grounded findings independently,
  with a single synthesis step assembling the cited answer. A narrow,
  already-scoped question (the same section's project-scoped example) has no
  reason to fan out at all — the coordinating step decides based on how
  broad the question is, not a fixed rule.
- **Periodic knowledge/health review, per concept folder.** M14's weekly
  review is naturally decomposable the same way: one sub-agent per concept
  folder assesses what changed, what's newly connected, and what's gone
  stale, and a coordinating step assembles the single review queue a person
  actually sees — rather than one agent attempting the whole vault in one
  pass.
- **Adversarial verification of a synthesized claim.** Rather than the same
  agent that wrote a `knowledge/` synthesis also being the one that checks
  it, a separate verification sub-agent can be tasked specifically with
  trying to *refute* a proposed claim — searching for contradicting
  evidence — before it's presented as settled. This is the same principle as
  independent verification in the build process above, applied to generated
  content instead of generated code.
- **A second, independent extraction pass as a confidence signal.** The
  semantic-graph roadmap's exploratory cross-validation idea (a cheap, local,
  non-generative model run alongside the primary extraction) is another
  instance of this pattern: two independent processes checking the same
  evidence, with agreement as the trust signal, rather than one process
  trusted alone.

In every case above, fan-out is an implementation detail of a stage already
described in Section 4 — it changes how many calls happen and in what order,
never what's allowed to happen without grounding, validation, or human
approval.

## 8. Evaluation Strategy

Every milestone in Section 6 carries its own numbered acceptance criteria —
that remains the baseline definition of "done" for a given piece of scope.
This section adds the cross-cutting checks that apply regardless of which
milestone is being evaluated, and the discipline for scaling anything up
safely.

### Cross-cutting dimensions

- **Grounding.** Can every generated claim be traced to a real quote at a
  real, currently-valid location? Spot-check a sample by hand, not just by
  running the automated validator that already checks this structurally.
- **Reproducibility.** Run the same extraction or synthesis twice on
  unchanged input. Measure agreement (entity/relationship overlap); do not
  assume determinism just because the schema validated both times.
- **Reversibility.** Can any filing, generated note, or repair be undone
  cleanly, leaving no trace beyond the audit record itself?
- **Vault integrity.** Before and after any operation that should not modify
  the vault, its content hash should be identical. This is a cheap,
  mechanical check worth automating, not just asserting.
- **Cost.** Track token and time cost per unit of work (per note, per
  document, per folder) before deciding to scale to a larger corpus — cost
  that looks negligible on one file does not always stay negligible at scale.
- **Human-judged usefulness.** Structural validity is necessary but not
  sufficient. A sample of generated output should be read by a person and
  judged useful and accurate, not merely well-formed.

### Staged rollout discipline

New extraction or synthesis capability is proven at increasing scale, with an
explicit go/no-go check before each step — never introduced directly at
vault scale:

1. **One controlled note.** A small, well-understood test note. Confirms the
   mechanism works at all.
2. **One real note.** An actual vault note, previously untouched by this
   capability. Confirms it survives contact with real, messy content.
3. **One folder.** A complete, bounded real collection. Confirms cost,
   runtime, and cross-document behavior at a small but real scale.
4. **The vault.** Only after the above have passed review, and only with
   monitoring in place to catch degradation early.

A capability that fails at any stage is fixed or re-scoped before advancing,
not pushed forward with a caveat.

## 9. Related

- [[Vision]] — the principles this specification operationalizes.
- [[Markdown Memory Architecture]] — the detailed provenance and generated-note
  lifecycle rules referenced in Sections 2–4.
- [[Semantic Graph Extraction Roadmap]] — the detailed phased plan behind the
  semantic-graph mention in Sections 3–4.
- `markos-design/CHANGELOG.md` — the running design-progress log described in
  Section 6's Recording Progress.
