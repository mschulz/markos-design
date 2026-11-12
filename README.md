# MarkOS

MarkOS is a cloud-hosted, event-driven personal agent operating system. The current design replaces the original PARA/Markdown/DuckDB system; the imported code is preserved history, not the foundation of the new runtime.

RabbitMQ is the internal event bus. Supabase PostgreSQL owns durable events, tasks, schedules, structured knowledge and conversations. Immutable source originals live in cloud object storage (provider pending). Generated Markdown is a derived view/export in an ordinary folder, without PARA or Obsidian requirements. Always-on services run on DigitalOcean with Docker Compose, independently of the MacBook.

GitHub is canonical for all project code, tests, infrastructure and documentation; Space is for drafting. Runtime data and secrets never belong here.

M0-A is merged as an independent contracts/configuration project;
see [the implementation record](MarkOS%20v0.3%20M0-A%20Contracts%20and%20Configuration.md).
Its preflight validates synthetic documents only. M0-B adds [explicit PostgreSQL storage](MarkOS%20v0.3%20M0-B%20Durable%20Storage.md) in main; M0-C adds [explicit RabbitMQ transport](MarkOS%20v0.3%20M0-C%20RabbitMQ%20Transport.md) in main. M0-D adds [synthetic execution and persistent scheduling](MarkOS%20v0.3%20M0-D%20Execution%20and%20Scheduling.md) for review. No deployed service or real agent/tool execution is implemented.

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

Main contains the historical v0.2 application imported through PR #5. PR #6's pure contract/config work was merged into an earlier feature branch, not main. PR #7 is closed unmerged with its branch retained; its legacy naming cleanup is not the replacement-system baseline. Neither legacy regression counts nor pure-schema checks establish a working RabbitMQ/PostgreSQL runtime.

The independent M0-A project under `services/runtime/` has its own package, dependency lock, tests and validation-only entry point. It does not import the old `markos` package or require a Markdown folder, PARA tree or DuckDB. M0-A–M0-C contracts, PostgreSQL storage and RabbitMQ transport are merged; synthetic execution/scheduling are now implemented for review; real agents, full replay/recovery and service lifecycle remain future work.

## Repository boundaries

| Path | Role |
|---|---|
| Root Title Case documents | Current decisions plus explicitly classified historical records |
| `src/markos/`, root `pyproject.toml` and `uv.lock` | Archived application and its reproducible environment; no new product foundation |
| `tests/` | Preserved legacy regression tests; independent runtime acceptance tests live under services/runtime/tests |
| `services/runtime/` | Independent contracts/configuration, PostgreSQL storage and RabbitMQ transport and M0-D synthetic execution/scheduling, own tests and lock |
| `config/`, `infrastructure/` | Versioned schemas/examples and reviewed infrastructure; legacy examples clearly separated |
| `.github/`, `Templates/` | Review workflow and document template |

## Preserving old work

Old source, evaluation results and Git ancestry remain recoverable. The v0.2 guide/project-memory snapshots and import manifest retain their original bytes. Their instructions, local paths and PARA conventions are historical, not directions to access personal notes. See the authority index before using any old document.

To reproduce the archived application's checks in an isolated synthetic environment, use Python 3.13 and uv: `uv sync --locked`, `uv lock --check`, `uv run ruff check .`, `uv run mypy`, `uv run pytest`. These checks protect the archive; they do not select the new architecture or complete M0.

## Documentation checks

The `Documentation checks` GitHub Actions job runs on pull requests and pushes to main. It checks the complete tracked Markdown inventory, required metadata and local links in current/template documents, then whitespace in the change. Historical documents remain inventoried; their old links are not current requirements. External URLs and heading anchors are not verified by this local-path checker. Run it without application dependencies:

```sh
python3 -B -m unittest discover -s scripts/tests -v
python3 -B scripts/check_docs.py
git diff --check
```

The workflow uses a pinned checkout action, read-only repository permissions, no persisted checkout credentials and no runtime services or deployment. This adds CI, not branch protection; making the check required is a separate repository setting. It does not validate MCP interoperability or complete runtime acceptance.

## Working agreement

Use focused `codex/` branches and reviewable PRs to main. Keep implementation, acceptance evidence and docs together. No merge, deployment, provisioning or personal-folder migration is performed by this increment. The separate `markos` repository remains untouched; no second active source of truth is introduced.
