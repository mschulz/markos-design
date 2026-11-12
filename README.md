# MarkOS

MarkOS augments human thinking through source-grounded knowledge and human-controlled assistance. This repository is the canonical GitHub home for its project artifacts: code, tests, infrastructure and documentation. ChatGPT Space is for collaborative drafting; approved changes are incorporated through pull requests.

This M0.0 branch imports the existing markos v0.2 application and tests while
preserving both repositories' Git histories. The original markos repository is
unchanged. The import is reviewed but not yet on main; promotion is pending.
No v0.3 runtime services are implemented.
See [the import record](MarkOS%20v0.3%20M0.0%20Import%20Record.md) for provenance,
file mapping and regression evidence.

## Start here

- [M0.1 contracts and configuration](MarkOS%20v0.3%20M0.1%20Contracts%20and%20Configuration.md): strict synthetic contracts, explicit runtime preflight and validation evidence.

- [Vision](Vision.md): the existing governing principles.
- [MarkOS v0.3 Specification](MarkOS%20v0.3%20Specification.md): complete Gmail-first event-system **review draft**, not approved for runtime implementation.
- [MarkOS v0.3 M0 Implementation Plan](MarkOS%20v0.3%20M0%20Implementation%20Plan.md): inspected existing code, import strategy, ordered changes and acceptance matrix.
- [MarkOS v0.3 Roadmap](MarkOS%20v0.3%20Roadmap.md): prior-architecture reconciliation, repository transition, approval gates and new milestones.
- [ADR-003 RabbitMQ Internal Event Bus](ADR-003%20RabbitMQ%20Internal%20Event%20Bus.md): proposed AMQP transport, durable handoff, retry and recovery contract.
- [ADR-002 Cloud-first Deployment](ADR-002%20Cloud-first%20Deployment.md): accepted DigitalOcean VM, Docker Compose and Supabase PostgreSQL baseline.
- [MarkOS Cloud Deployment Architecture](MarkOS%20Cloud%20Deployment%20Architecture.md): scheduling, restart policies, backups, monitoring and recovery requirements.
- [ADR-0002 GitHub as Canonical Project Record](ADR-0002%20GitHub%20as%20Canonical%20Project%20Record.md): accepted project governance and PR workflow.
- [MarkOS Specification](MarkOS%20Specification.md): preserved earlier architecture and M0–M16 history.
- [Markdown Memory Architecture](Markdown%20Memory%20Architecture.md), [Semantic Graph Extraction](Semantic%20Graph%20Extraction.md), [Semantic Graph Extraction Roadmap](Semantic%20Graph%20Extraction%20Roadmap.md) and [Graphify Evaluation](Graphify%20Evaluation.md): existing knowledge design, experiments and subsystem plan.
- [Changelog](CHANGELOG.md): design progress.

Production hosting is accepted: always-on services run on DigitalOcean with Docker Compose and durable operational state in Supabase PostgreSQL. The MacBook is development/admin only; shutting it down must not affect jobs. Runtime milestones, provisioning and deployment remain subject to separate approval.

v0.3's PostgreSQL-backed knowledge, semantic retrieval and web/PWA direction differ from the existing Markdown, DuckDB and desktop design. Review those differences in the roadmap before implementation; Vision remains unchanged.

## Local development and regression checks

Use Python 3.13 and uv in an isolated checkout outside synced folders. Set
`UV_PROJECT_ENVIRONMENT` to `$PWD/venv` (or review/use `.envrc`), then run:

```console
uv sync --locked
uv lock --check
uv run ruff check .
uv run mypy
uv run pytest
```

The installed `markos` CLI retains the v0.2 read-only behavior. Start with
`uv run markos --help` and `uv run markos version`. Supply explicit paths or a
reviewed configuration based on `config/legacy.example.toml`; the package
configuration selects no real vault or index. The synthetic example paths are
placeholders, not an instruction to access personal notes.

[MarkOS v0.2 User Guide](MarkOS%20v0.2%20User%20Guide.md) is the exact historical
source README, including its original repository URL. Its old clone instructions
and [project memory](MarkOS%20v0.2%20Project%20Memory.md) are historical; current
repository organization and instructions are here and in `AGENTS.md`.

## Repository organization

Existing documents remain flat at the root to preserve established paths. The [architecture document template](Templates/Architecture%20Document%20Template.md) remains in `Templates/`.

| Path | Purpose |
|---|---|
| [src/markos/](src/markos/README.md) | Imported read-only v0.2 application; future runtime under `markos.runtime` |
| [tests/](tests/README.md) | Imported pytest suite, import guards and future runtime tests |
| [config/](config/README.md) | Configuration schemas and sanitized examples |
| [infrastructure/](infrastructure/README.md) | Infrastructure definitions and operational documentation |
| [.github/](.github/PULL_REQUEST_TEMPLATE.md) | Review template; executable CI deferred until toolchain selection |

## Milestone status

The earlier specification records **M0–M10 delivered** in the separate implementation repository and **M11–M16 accepted**. Those records are preserved and do not establish completion of the proposed **v0.3 M0–M8**, whose runtime slices remain unstarted; M0.0 import is prepared for review.

## Contributing

Read [AGENTS.md](AGENTS.md), the applicable specification and the roadmap. Use a focused `codex/` branch and pull request; include scope, validation and unresolved decisions. Implementation milestones include automated tests and documentation. Keep proposal and acceptance status explicit.

Do not commit credentials, private notes, emails, bills or runtime data. This increment does not deploy code or modify an Obsidian vault.
