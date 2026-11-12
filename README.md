# MarkOS

MarkOS is a cloud-hosted, event-driven personal agent operating system. The current design replaces the original PARA/Markdown/DuckDB system; the imported code is preserved history, not the foundation of the new runtime.

RabbitMQ is the internal event bus. Supabase PostgreSQL owns durable events, tasks, schedules, structured knowledge and conversations. Immutable source originals live in cloud object storage (provider pending). Generated Markdown is a derived view/export in an ordinary folder, without PARA or Obsidian requirements. Always-on services run on DigitalOcean with Docker Compose, independently of the MacBook.

GitHub is canonical for all project code, tests, infrastructure and documentation; Space is for drafting. Runtime data and secrets never belong here.

MCP access for agents is proposed through a governed tool gateway with explicit owner/domain grants, scoped approvals and durable audit. It is not enabled by this documentation; RabbitMQ remains the event bus.

## Read first

- [Architecture reset decision](ADR-004%20Event-driven%20Architecture%20Supersedes%20Legacy%20MarkOS.md): accepted replacement of the legacy baseline, evidence and limits.
- [Vision](Vision.md), [v0.3 specification](MarkOS%20v0.3%20Specification.md) and [roadmap](MarkOS%20v0.3%20Roadmap.md): current product and architecture.
- [M0 implementation plan](MarkOS%20v0.3%20M0%20Implementation%20Plan.md): independent runtime, ordered changes and acceptance tests.
- [Documentation authority index](Documentation%20Authority%20Index.md): current requirements versus archived designs and evidence.
- [Agent MCP access ADR](ADR-005%20Governed%20MCP%20Access%20for%20Agents.md): proposed permissions, domain isolation, approvals, audit and acceptance gates.
- [RabbitMQ ADR](ADR-003%20RabbitMQ%20Internal%20Event%20Bus.md): selected transport and proposed operating profile.
- [Cloud deployment architecture](MarkOS%20Cloud%20Deployment%20Architecture.md) and [cloud decision](ADR-002%20Cloud-first%20Deployment.md): hosting, durable scheduling, backup and recovery.
- [Canonical project record](ADR-0002%20GitHub%20as%20Canonical%20Project%20Record.md): GitHub and review workflow.

## Implementation status

Main contains the historical v0.2 application imported through PR #5. PR #6's pure contract/config work was merged into an earlier feature branch, not main. PR #7's legacy naming cleanup is not the replacement-system baseline. Neither legacy regression counts nor pure-schema checks establish a working RabbitMQ/PostgreSQL runtime.

The new independent runtime is planned under `services/runtime/` with its own package, dependency lock and tests. It is not implemented by this documentation correction. No new runtime may import the old `markos` package or require a Markdown folder, PARA tree or DuckDB.

## Repository boundaries

| Path | Role |
|---|---|
| Root Title Case documents | Current decisions plus explicitly classified historical records |
| `src/markos/`, root `pyproject.toml` and `uv.lock` | Archived application and its reproducible environment; no new product foundation |
| `tests/` | Preserved legacy regression tests; new runtime acceptance tests will be independent |
| `services/runtime/` (planned) | Independent runtime project, package `markos_runtime`, own tests and lock |
| `config/`, `infrastructure/` | Versioned schemas/examples and reviewed infrastructure; legacy examples clearly separated |
| `.github/`, `Templates/` | Review workflow and document template |

## Preserving old work

Old source, evaluation results and Git ancestry remain recoverable. The v0.2 guide/project-memory snapshots and import manifest retain their original bytes. Their instructions, local paths and PARA conventions are historical, not directions to access personal notes. See the authority index before using any old document.

To reproduce the archived application's checks in an isolated synthetic environment, use Python 3.13 and uv: `uv sync --locked`, `uv lock --check`, `uv run ruff check .`, `uv run mypy`, `uv run pytest`. These checks protect the archive; they do not select the new architecture or complete M0.

## Working agreement

Use focused `codex/` branches and reviewable PRs to main. Keep implementation, acceptance evidence and docs together. No merge, deployment, provisioning or personal-folder migration is authorized by this documentation update. The separate `markos` repository remains untouched; no second active source of truth is introduced.
