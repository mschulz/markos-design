# MarkOS Design — Project Memory

This file contains durable project context and working agreements for coding
agents operating in this repository. Keep it concise, current, and free of
secrets.

## Project Purpose

- GitHub is the canonical record for all MarkOS code, tests, infrastructure,
  configuration schemas/examples and documentation. Space is for collaborative
  drafting; approved changes enter GitHub through pull requests.
- Use this repository for code and docs together going forward. Application
  code currently remains in the separate `markos` repository; inspect it and
  preserve its history in a reviewed migration before importing implementation.
- See [[ADR-0002 GitHub as Canonical Project Record]] for accepted governance.

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

- [[ADR-003 RabbitMQ Internal Event Bus]] proposes RabbitMQ over AMQP 0-9-1
  for internal events, replacing provisional MQTT/Mosquitto. Keep PostgreSQL
  outbox/inbox and task truth; MQTT is an optional device adapter only. Preserve
  accepted cloud placement. This is a proposal, not runtime/deployment approval.

- [[ADR-002 Cloud-first Deployment]] accepts DigitalOcean VM / Docker Compose
  for always-on production services and Supabase PostgreSQL for durable state.
  MacBook is development/admin only. Follow [[MarkOS Cloud Deployment Architecture]]
  for persistent scheduling, restart policies, backups, monitoring and recovery.
  Preserve the distinct existing ADR-0002 governance record; use full ADR titles.
  This decision does not authorize provisioning, deployment or runtime milestones.

- [[MarkOS v0.3 Specification]] contains the complete proposed Gmail-first
  event-system draft. [[MarkOS v0.3 Roadmap]] reconciles it with the prior
  architecture and defines the migration and approval gates. Runtime v0.3
  implementation is not authorized by this documentation baseline.
- Keep earlier M0–M16 and new v0.3 M0–M8 milestone labels distinct. Do not
  transfer old completion statuses or treat provisional technology as approved.
- [[Vision]] remains unchanged. Markdown knowledge authority, semantic retrieval
  and desktop direction require explicit review before superseding prior design.

- `MarkOS Specification.md` is the preserved earlier consolidated document: purpose,
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

- Concrete implementation boundaries now exist in `src/markos/`, `tests/`,
  `config/`, `infrastructure/` and `.github/`. Keep architecture documents flat;
  do not create category folders or relocate prior artifacts in this increment.

## Working Agreements

- Review changes on a focused `codex/` branch and open a pull request to
  `main`. Do not commit directly to main or merge the handoff PR automatically.
  This supersedes the earlier direct-to-main design workflow. Each runtime
  milestone needs explicit scope approval, contracts, automated tests and docs.
- Record design-level progress (decisions accepted, evaluations run,
  milestones accepted or completed) in `CHANGELOG.md` as it happens.
  Implementation progress stays in its repository's git history until migration;
  imported and new implementation progress will be recorded here with tests.
- Do not deploy code or modify an Obsidian vault in this handoff. Use synthetic
  fixtures; live runtime data never belongs in GitHub.
- Never store credentials, tokens, private note contents, or other secrets
  in this repository.
