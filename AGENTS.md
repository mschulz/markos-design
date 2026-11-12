# MarkOS Design — Project Memory

This file contains durable project context and working agreements for coding
agents operating in this repository. Keep it concise, current, and free of
secrets.

## Project Purpose

- GitHub is the canonical record for all MarkOS code, tests, infrastructure,
  configuration schemas/examples and documentation. Space is for collaborative
  drafting; approved changes enter GitHub through pull requests.
- This import branch contains the existing markos v0.2 application and tests,
  alongside the canonical design documents. Its two-parent import preserves
  source history; the original markos repository remains unchanged. M0.0 has
  been promoted to main; this branch retains reviewed M0.1 work and
  canonical `markdown_root` naming for an ordinary Markdown folder.
- See [[MarkOS v0.3 M0.0 Import Record]] for source/design anchors, file mapping,
  conflict resolutions and validation. [[MarkOS v0.2 Project Memory]] and
  [[MarkOS v0.2 User Guide]] are historical source snapshots, not current policy.
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
- `Attachments/` is an optional plain folder for document attachments.
  No editor-specific folder, metadata or API is required.
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
  Markdown root structure, the knowledge pipeline, where AI is used, the final
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

## Python and application boundaries

- Canonical folder naming is `markdown_root`, `--markdown-root` and
  `MARKOS_MARKDOWN_ROOT`; see [[Markdown Root Usage]]. Deprecated legacy names
  exist only for compatibility. No editor metadata, API or installation is
  required. Preserve historical import/evaluation records as evidence.

- Preserve Python 3.13, uv and the source `uv.lock`; do not add runtime dependencies
  in M0.0. Use the reviewed `.envrc` or explicitly set `UV_PROJECT_ENVIRONMENT`
  to a non-hidden `venv` in an isolated checkout outside synced folders.
- After code changes run `uv lock --check`, `uv run ruff check .`, `uv run mypy`
  and `uv run pytest`. Strict mypy covers `src/markos` and `tests`.
- The imported CLI, index, local API and watcher stay read-only. Index paths
  remain outside Markdown folders; failed rebuilds preserve the last valid index, current
  sync is byte-identical, and rebuilds remain serialized.
- Use synthetic temporary Markdown folders and injected clocks. External LLM disclosure
  remains a separate explicit permission; tests mock provider calls.
- `pyproject.toml` selects no Markdown root/index. Use explicit CLI/env settings or a
  reviewed config copied from `config/legacy.example.toml`. Do not use a real
  Markdown root during development or tests. The local API is not a public cloud API.
- M0.1 adds strict synthetic contracts and explicit validation-only preflight
  under `markos.runtime`. Runtime settings never discover legacy config or Markdown folders.
  No RabbitMQ/PostgreSQL adapter, service loop or `markos-runtime` entry point
  exists yet. See [[MarkOS v0.3 M0.1 Contracts and Configuration]].
- Check generated schema drift with
  `uv run python -m markos.runtime.schemas --check` after contract changes.

## Working Agreements

- Review changes on a focused `codex/` branch and open a pull request to
  `main`. Do not commit directly to main or merge the handoff PR automatically.
  This supersedes the earlier direct-to-main design workflow. Each runtime
  milestone needs explicit scope approval, contracts, automated tests and docs.
- Record design-level progress (decisions accepted, evaluations run,
  milestones accepted or completed) in `CHANGELOG.md` as it happens.
  Implementation progress stays in its repository's git history until migration;
  imported and new implementation progress will be recorded here with tests.
- Do not deploy code or modify a personal Markdown folder in this handoff. Use synthetic
  fixtures; live runtime data never belongs in GitHub.
- Never store credentials, tokens, private note contents, or other secrets
  in this repository.
