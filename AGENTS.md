# MarkOS — Current Project Instructions

## Authority and architecture

- Follow [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[Vision]], [[MarkOS v0.3 Specification]] and [[MarkOS v0.3 M0 Implementation Plan]]. The owner abandoned the original design; do not reconcile the new product back into PARA, canonical Markdown, DuckDB or desktop-first assumptions.
- RabbitMQ over AMQP is the selected internal event bus. PostgreSQL owns durable operational state and structured knowledge; originals live in access-controlled object storage. Generated Markdown is a view/export, with no required PARA layout or Obsidian dependency.
- DigitalOcean / Docker Compose and Supabase are the accepted hosting baseline. Production scheduling and agents must survive the MacBook being off.
- Use [[Documentation Authority Index]] to distinguish current decisions, proposals, historical evidence and exact archived snapshots. Superseded instructions never override this file or the owner's latest decisions.

- Follow [[ADR-005 Governed MCP Access for Agents]] for the proposed MCP boundary: event subscriptions grant no tools; deny by default, isolate owner/domain credentials/data, require scoped disclosure/effect approval and durable pre-dispatch audit. This documentation enables no MCP access and adds no M0 dependency.

## Project source and implementation boundaries

- GitHub `markos-design` is canonical for all project artifacts. Space is drafting material. Keep runtime data, personal notes and secrets outside GitHub.
- Existing `src/markos/`, root package/lock and legacy tests are a preserved archive, not the new runtime base. Do not add v0.3 features there, expose its local API as the new gateway, or make the new runtime depend on it.
- M0-A is merged under `services/runtime/`, package `markos_runtime`, with its own pyproject, lock, tests and validation-only entry point. See [[MarkOS v0.3 M0-A Contracts and Configuration]]. Python 3.13 and Pydantic were selected/tested independently; do not inherit the archived root lock. Merged M0-B adds explicit PostgreSQL storage; merged M0-C adds explicit RabbitMQ library adapters. M0-D adds fixed synthetic no-op execution and persistent scheduling for review. The CLI remains validation-only; real agents/tools and a deployed service lifecycle are not implemented. See [[MarkOS v0.3 M0-D Execution and Scheduling]]. See [[MarkOS v0.3 M0-B Durable Storage]] and [[MarkOS v0.3 M0-C RabbitMQ Transport]].
- PR #6 contracts may be evaluated and adapted only with explicit review of their new-system fit. PR #7 is legacy naming work, not a prerequisite. No import/promotion is required to begin the independent design.
- Preserve source history, exact v0.2 guide/project-memory snapshots and import manifest. Do not delete source or relocate the separate `markos` repository in this correction.

## Documentation and review

- Keep architecture docs flat at root as Title Case Markdown, with existing paths preserved. Use `Templates/Architecture Document Template.md` and exact-title wikilinks.
- Maintain status, version, owner, created/updated/reviewed metadata. Mark superseded material clearly; never let an old future roadmap read as current instructions.
- Use a focused `codex/` branch and PR to main. Do not merge automatically. Each implementation slice includes contracts, tests, docs and unresolved decisions. Record progress in CHANGELOG.
- Accepted architecture does not imply deployment authorization. ADR-003 broker selection is accepted; its version/resource/retention profile remains proposed until verified and measured.

## Validation and data access

- Use synthetic fixtures and isolated services. No personal notes, Gmail accounts, provider calls, production credentials, cloud provisioning or deployment in this correction.
- Documentation-only changes run `python3 -B -m unittest discover -s scripts/tests -v`, `python3 -B scripts/check_docs.py` and `git diff --check`. Keep every tracked Markdown file classified in the authority index; current local links resolve and metadata is complete. The Documentation workflow does not run legacy/runtime dependencies or verify external URLs/anchors. To verify the archived code was preserved, its existing gate is `uv lock --check`, `uv run ruff check .`, `uv run mypy`, `uv run pytest` in an isolated Python 3.13 uv environment.
- Run independent checks from `services/runtime/` with an explicit separate `UV_PROJECT_ENVIRONMENT`: `uv lock --check`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, and `uv run python -m markos_runtime.schemas --output-dir ../../config/runtime/schemas`. Preserve the root archive environment.
- Storage integration needs an explicit dedicated local `MARKOS_TEST_ADMIN_DSN`; it creates/drops synthetic test databases/roles. CI requires these cases. Never point tests at shared/live data. Migrations run only through the explicit migration-role API, never preflight or worker startup.
- Real broker tests require disposable local RabbitMQ/management endpoints; CI requires both broker and PostgreSQL suites. Runtime identities never configure/bind arbitrary topology; management auditing uses a separate read-only identity. Preserve migration 0001; transport state is additive migration 0002. Preserve both; execution/scheduling is additive migration 0003. Only synthetic no-op execution is allowed, with DB-clock fencing and a shared two-lease budget.
- New acceptance must prove independent dependency/import boundaries, strict event/identity validation, atomic event/outbox and inbox/task state, confirms/returns, replay, scheduler recovery and owner/domain isolation against isolated PostgreSQL/RabbitMQ. Legacy checks are not substitutes.
- Keep consequential effects disabled in M0; approval, privacy, provenance and no secrets in messages/logs remain requirements.
