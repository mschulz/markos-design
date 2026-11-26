# CLAUDE.md

Rules that apply everywhere in this repository. `AGENTS.md` points here for other
tools. **`docs/decisions.md` holds the reasons:** a rule marked `→ decisions: X`
has a section *X* there. Read it before you weaken or remove a rule. `README.md`
is user-facing: keep implementation detail out of it.

## Project overview

MarkOS is a personal knowledge base and agent system. **Current priority: the
electricity-bill workflow (K1)**: supply a bill, keep the original, extract
figures, inspect them, ask questions with cited answers. K1-A (pasted text) is
merged; K1-B (text-based PDF with page citations) is next. Spec:
`docs/k1-bill-knowledge.md`. Gmail, broad classification and real
agents come later.

**All code is in `src/markos_runtime/`** (one `pyproject.toml` and `uv.lock`
at the repo root). Migrations are in `infrastructure/migrations/`.
→ decisions: One project at the repo root: package `markos_runtime`

**The v0.2 design is abandoned.** Never reintroduce PARA, DuckDB, Obsidian or
Markdown as the source of truth. → decisions: v0.2 is abandoned; v0.3 is an
independent event-driven runtime

## Architecture

- **PostgreSQL owns durable state, facts and provenance.** Originals go to object
  storage unchanged. Markdown is only an export. → decisions: PostgreSQL owns
  durable state; Markdown is only an export
- **RabbitMQ is the event bus, never the record.** → decisions: RabbitMQ is the
  internal event bus, not the record
- **Owner and domain come from authenticated identity**, never from request
  input. Check every read and write against current authority. → decisions: Owner
  and domain come from authenticated identity, never from input
- **Every route and capability is gated by an explicit config object**
  (`Synthetic*ApiConfig`). A new feature gets its own gate, off by default.
  → decisions: Explicit config objects gate every route and capability
- **Bill answers are deterministic and cited.** Uncertain values show "Needs
  review". Future user corrections stay separate from the original extraction;
  never rewrite evidence. → decisions: Bill answers are deterministic, cited and
  uncertain when unsure

## Database

- **Migrations are append-only.** Never edit `0001`–`0019`; the next is `0020`.
  → decisions: Migrations are append-only and checksummed
- **Runtime processes never run migrations**; only the migration-role API does.
  → decisions: Runtime processes never run migrations
- **Database time is the only clock.** → decisions: Database time is the only clock

## Testing

Run checks from the repo root with a separate `UV_PROJECT_ENVIRONMENT`;
commands are in `docs/development.md`.

- **Database tests drop databases and create roles.** Point
  `MARKOS_TEST_ADMIN_DSN` only at a disposable local cluster. **Never at port
  5432 (the Mac's own PostgreSQL), 15432 or 25432 (previews).** The test guard
  refuses those ports; CI uses 55432. → decisions:
  Tests use disposable databases and brokers only
- **A skipped test is not evidence.** CI requires every service. → decisions: CI
  fails on missing services instead of skipping
- **Destructive drills run only in GitHub-hosted CI.** → decisions: Destructive
  drills run only on owned, labelled, ephemeral projects
- Documentation: `python3 -B scripts/check_docs.py` fails on broken local links.

## Data and secrets

- **Synthetic data only.** Real accounts, Gmail, models, personal bills and
  deployment stay off until Mark enables them. → decisions: Everything runs
  synthetic; real accounts, providers and effects are off
- **Never commit or log secrets, DSNs, tokens or source content.** → decisions:
  Logs and messages never carry secrets or payloads

## Local preview

The current preview runs from this repo (PostgreSQL on 25432). Its settings are in
`~/.config/markos/preview.env`; see `docs/local-preview.md`. An older preview
(PostgreSQL on 15432) is paused under `~/Documents/Codex`. Both use the same
fixed ports, so only one runs at a time. **Never reprovision or delete either
preview** without Mark's instruction.

## Workflow

- **One focused branch and PR per change.** Ship the code, tests and docs
  together. Add a `CHANGELOG.md` entry.
- **Never merge, deploy or push to `main` without Mark's go-ahead.**
- **Lean delivery:** show working product results early; automate regression
  tests rather than asking Mark to run them. → decisions: Lean delivery: runnable
  feedback first, real gates before real data

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
