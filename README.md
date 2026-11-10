# MarkOS

MarkOS augments human thinking through source-grounded knowledge and human-controlled assistance. This repository is the canonical GitHub home for its project artifacts: code, tests, infrastructure and documentation. ChatGPT Space is for collaborative drafting; approved changes are incorporated through pull requests.

The current tree contains design documents and documented boundaries for future implementation. Existing software is recorded in the separate `markos` repository; it has not been migrated or reverified here.

## Start here

- [Vision](Vision.md): the existing governing principles.
- [MarkOS v0.3 Specification](MarkOS%20v0.3%20Specification.md): complete Gmail-first event-system **review draft**, not approved for runtime implementation.
- [MarkOS v0.3 Roadmap](MarkOS%20v0.3%20Roadmap.md): prior-architecture reconciliation, repository transition, approval gates and new milestones.
- [ADR-0002 GitHub as Canonical Project Record](ADR-0002%20GitHub%20as%20Canonical%20Project%20Record.md): accepted project governance and PR workflow.
- [MarkOS Specification](MarkOS%20Specification.md): preserved earlier architecture and M0–M16 history.
- [Markdown Memory Architecture](Markdown%20Memory%20Architecture.md), [Semantic Graph Extraction](Semantic%20Graph%20Extraction.md), [Semantic Graph Extraction Roadmap](Semantic%20Graph%20Extraction%20Roadmap.md) and [Graphify Evaluation](Graphify%20Evaluation.md): existing knowledge design, experiments and subsystem plan.
- [Changelog](CHANGELOG.md): design progress.

v0.3's PostgreSQL-backed knowledge, semantic retrieval and web/PWA direction differ from the existing Markdown, DuckDB and desktop design. Review those differences in the roadmap before implementation; Vision remains unchanged.

## Repository organization

Existing documents remain flat at the root to preserve established paths. The [architecture document template](Templates/Architecture%20Document%20Template.md) remains in `Templates/`.

| Path | Purpose |
|---|---|
| [src/markos/](src/markos/README.md) | Future application, worker and agent code |
| [tests/](tests/README.md) | Future tests and synthetic fixtures |
| [config/](config/README.md) | Configuration schemas and sanitized examples |
| [infrastructure/](infrastructure/README.md) | Infrastructure definitions and operational documentation |
| [.github/](.github/PULL_REQUEST_TEMPLATE.md) | Review template; executable CI deferred until toolchain selection |

## Milestone status

The earlier specification records **M0–M10 delivered** in the separate implementation repository and **M11–M16 accepted**. Those records are preserved and do not establish completion of the proposed **v0.3 M0–M8**, all of which remain unstarted.

## Contributing

Read [AGENTS.md](AGENTS.md), the applicable specification and the roadmap. Use a focused `codex/` branch and pull request; include scope, validation and unresolved decisions. Implementation milestones include automated tests and documentation. Keep proposal and acceptance status explicit.

Do not commit credentials, private notes, emails, bills or runtime data. This increment does not deploy code or modify an Obsidian vault.
