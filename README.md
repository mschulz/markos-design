# MarkOS

MarkOS is a cloud-hosted, event-driven personal agent operating system. The current design replaces the original PARA/Markdown/DuckDB system; the imported code is preserved history, not the foundation of the new runtime.

RabbitMQ is the internal event bus. Supabase PostgreSQL owns durable events, tasks, schedules, structured knowledge and conversations. Immutable source originals live in cloud object storage (provider pending). Generated Markdown is a derived view/export in an ordinary folder, without PARA or Obsidian requirements. Always-on services run on DigitalOcean with Docker Compose, independently of the MacBook.

GitHub is canonical for all project code, tests, infrastructure and documentation; Space is for drafting. Runtime data and secrets never belong here.

M0-A is merged as an independent contracts/configuration project;
see [the implementation record](MarkOS%20v0.3%20M0-A%20Contracts%20and%20Configuration.md).
Its preflight validates synthetic documents only. M0-B adds [explicit PostgreSQL storage](MarkOS%20v0.3%20M0-B%20Durable%20Storage.md) in main; M0-C adds [explicit RabbitMQ transport](MarkOS%20v0.3%20M0-C%20RabbitMQ%20Transport.md) in main. M0-D [synthetic execution and persistent scheduling](MarkOS%20v0.3%20M0-D%20Execution%20and%20Scheduling.md), M0-E [synthetic replay, deletion safeguards and retention](MarkOS%20v0.3%20M0-E%20Replay%20and%20Retention.md) and M0-F [synthetic lifecycle and closeout](MarkOS%20v0.3%20M0-F%20Lifecycle%20and%20Closeout.md) are merged through PRs #15–#17. The [M0 runbook](MarkOS%20v0.3%20M0%20Runbook.md) describes controlled test procedures and production gaps. No service is deployed and no real agent/tool execution is implemented.

MCP access for agents is proposed through a governed tool gateway with explicit owner/domain grants, scoped approvals and durable audit. It is not enabled by this documentation; RabbitMQ remains the event bus.

## Read first

- [Architecture reset decision](ADR-004%20Event-driven%20Architecture%20Supersedes%20Legacy%20MarkOS.md): accepted replacement of the legacy baseline, evidence and limits.
- [Vision](Vision.md), [v0.3 specification](MarkOS%20v0.3%20Specification.md) and [roadmap](MarkOS%20v0.3%20Roadmap.md): current product and architecture.
- [M0 implementation plan](MarkOS%20v0.3%20M0%20Implementation%20Plan.md): independent runtime, ordered changes and acceptance tests.
- [M1 control-plane implementation plan](MarkOS%20v0.3%20M1%20Control%20Plane%20Implementation%20Plan.md): merged-main review, proposed API/identity and seven implementation slices with 22 acceptance cases. M1-A/B are merged; the M1-C review record below distinguishes implemented fixture behavior from later gates.
- [Documentation authority index](Documentation%20Authority%20Index.md): current requirements versus archived designs and evidence.
- [Agent MCP access ADR](ADR-005%20Governed%20MCP%20Access%20for%20Agents.md): proposed permissions, domain isolation, approvals, audit and acceptance gates.
- [RabbitMQ ADR](ADR-003%20RabbitMQ%20Internal%20Event%20Bus.md): selected transport and proposed operating profile.
- [Cloud deployment architecture](MarkOS%20Cloud%20Deployment%20Architecture.md) and [cloud decision](ADR-002%20Cloud-first%20Deployment.md): hosting, durable scheduling, backup and recovery.
- [Canonical project record](ADR-0002%20GitHub%20as%20Canonical%20Project%20Record.md): GitHub and review workflow.

## Implementation status

Main contains the historical v0.2 application imported through PR #5. PR #6's pure contract/config work was merged into an earlier feature branch, not main. PR #7 is closed unmerged with its branch retained; its legacy naming cleanup is not the replacement-system baseline. Neither legacy regression counts nor pure-schema checks establish a working RabbitMQ/PostgreSQL runtime.

The independent M0-A project under `services/runtime/` has its own package, dependency lock, tests and connection-free preflight command. M0-F adds a separately invoked, explicitly confirmed test-only service command. It does not import the old `markos` package or require a Markdown folder, PARA tree or DuckDB. M0-A–M0-C contracts, PostgreSQL storage and RabbitMQ transport are merged; M0-D–M0-F synthetic execution, replay/retention and lifecycle are merged; real agents, source-provider access/deletion and production lifecycle remain future work. M0-F supplies disposable CI process/dependency/pressure/restore evidence with explicit production gaps.

## Repository boundaries

| Path | Role |
|---|---|
| Root Title Case documents | Current decisions plus explicitly classified historical records |
| `src/markos/`, root `pyproject.toml` and `uv.lock` | Archived application and its reproducible environment; no new product foundation |
| `tests/` | Preserved legacy regression tests; independent runtime acceptance tests live under services/runtime/tests |
| `services/runtime/` | Independent contracts/configuration, PostgreSQL storage and RabbitMQ transport and synthetic execution/scheduling and M0-E recovery/retention, own tests and lock |
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

## M1-A contracts merged

[M1-A Contracts and Identity](MarkOS%20v0.3%20M1-A%20Contracts%20and%20Identity.md) adds versioned synthetic control-plane models, schemas/examples, frozen test configuration and connection-free `preflight-control`. [ADR-006](ADR-006%20Control%20Plane%20Identity%20and%20Trust%20Boundary.md) records proposed identity/session/delegation decisions and production gates. An optional independently locked API/JWT/template profile is compatibility tested. No login service, control-plane database, chat execution or tool access is implemented; a valid principal/intent descriptor is not authority. PRs #18/#19 are merged; the original planning/contract evidence and all prerequisite history remain preserved.

## M1-B persistence prepared for review

[M1-B Control Plane Persistence](MarkOS%20v0.3%20M1-B%20Control%20Plane%20Persistence.md) adds explicit scoped PostgreSQL storage for sessions/memberships, versioned registry/configuration, conversations/messages/activity and idempotency, with a dedicated gateway role and additive migration 0006. Messages are staged as held proposals; the current RabbitMQ/no-op path is unchanged. M1-C supplies the fixture authentication/API in the separate review described below; publishable chat, approvals/tools and production deployment remain later work.


## Merged M1-B and M1-C review status

PR #20 is merged. [[MarkOS v0.3 M1-C Authenticated Shared API]] records the `6a87fb0` main review, green 533-case post-merge CI and a separately reviewable authenticated shared API. It adds an explicit fixture-only ASGI factory, browser/CLI PKCE session protocols, current signature/upstream/local authority checks, scoped registry/configuration/status/activity operations, private health and additive migration 0007. A separate loopback HTTPS profile keeps real accounts, chat and tools disabled. API metadata is not runtime knowledge authority; PostgreSQL/RabbitMQ and MCP/M3 decisions are preserved. Interactive web/CLI clients remain M1-E; chat promotion remains M1-D. Provider integration, key custody, scheduled secret cleanup, API packaging/process/restore and production gates remain open. No merge or deployment.
