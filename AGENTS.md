# MarkOS Design — Project Memory

This file contains durable project context and working agreements for coding
agents operating in this repository. Keep it concise, current, and free of
secrets.

## Project Purpose

- This repository holds MarkOS's architectural design, engineering decisions,
  and long-term roadmap. It contains no application code.
- The implementation lives in the separate `markos` repository, which has its
  own `AGENTS.md` for that repo's conventions.

## Repository Layout

- Documents live flat at the top level as Title Case `.md` files with
  spaces in filenames (e.g. `MarkOS Specification.md`), not nested inside
  category subfolders. This is deliberate, established by 11 days of actual
  practice, not an oversight — several category subfolders (`ADR/`,
  `Architecture/`, `Design Sessions/`, `Engineering/`, `Research/`,
  `Roadmap/`, `Standards/`, `00 Inbox/`) existed but were always empty and
  were removed on 2026-08-13. Do not recreate them without a concrete,
  identified reason; if a document doesn't fit at the top level, that's a
  signal to reconsider its scope, not to add a folder.
- `Attachments/` holds pasted images or files a document references — kept
  even though currently empty, since it's the standard Obsidian convention
  for that purpose.
- `Templates/` holds `Architecture Document Template.md`, the frontmatter and
  section skeleton for a new single-topic document. Use it rather than
  copying an arbitrary existing document and stripping its content.
- `CHANGELOG.md` is the running design-progress log, organized by design
  milestone under `Accepted` / `Completed` / `Evaluated` headings.

## Current State

- `MarkOS Specification.md` is the primary, consolidated document: purpose,
  vault structure, the knowledge pipeline, where AI is used, the final
  system, implementation milestones, agent/sub-agent usage, and evaluation
  strategy. It incorporates and supersedes the former `Milestones.md`,
  `Mac App Architecture.md`, and `PARA Migration Strategy.md`, all three
  retired and deleted — do not recreate them; extend the Specification's
  relevant section instead.
- `Vision.md` holds the five governing principles, meant to change rarely.
- `Markdown Memory Architecture.md` holds the detailed provenance and
  generated-note lifecycle rules the Specification only summarizes.
- `Graphify Evaluation.md` and `Semantic Graph Extraction.md` are evaluation
  records (what was tried, what the numbers were) — historical, not
  forward-looking. `Semantic Graph Extraction Roadmap.md` is the
  forward-looking phased plan for that same subsystem.
- Status: `status: draft` on `MarkOS Specification.md` means it is still
  being actively reconsidered turn by turn; do not treat it as settled
  without checking its `reviewed` date is current.

## Document Conventions

- Frontmatter: `title`, `type`, `status`, `version`, `owner`, `created`,
  `updated`, `reviewed`, `tags`. Keep `updated`/`reviewed` current when a
  document changes materially; bump `version` for a substantive revision,
  not for typo fixes.
- Cross-reference other documents with `[[Wikilink]]` syntax, matching the
  exact document title. When a document is retired or renamed, grep the
  whole repository for links to it and fix or remove every one — do not
  leave a wikilink pointing at a file that no longer exists.
- Prefer consolidating a *related* decision into `MarkOS Specification.md`
  over creating a new small file. A genuinely separable decision (a new
  subsystem evaluation, a new self-contained roadmap) still gets its own
  document, following `Templates/Architecture Document Template.md`.

## Working Agreements

- Review a document's content before committing it; commit directly to
  `main`. This repository intentionally does not use the pull-request
  process documented for `markos` in the Specification's Version Control
  section — there is no CI or mechanical acceptance criteria to gate on here.
- Record design-level progress (decisions accepted, evaluations run,
  milestones accepted or completed) in `CHANGELOG.md` as it happens.
  Implementation-level progress belongs in `markos`'s own git history, not
  here.
- Never store credentials, tokens, private note contents, or other secrets
  in this repository.
