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
`MarkOS v0.3 Lean Bill Knowledge MVP.md`. Gmail, broad classification and real
agents come later.

**All code is in `services/runtime/`** (package `markos_runtime`, own
`pyproject.toml` and `uv.lock`). Migrations are in `infrastructure/migrations/`.
→ decisions: One independent project: `services/runtime`, package `markos_runtime`

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

- **Migrations are append-only.** Never edit `0001`–`0014`; the next is `0015`.
  → decisions: Migrations are append-only and checksummed
- **Runtime processes never run migrations**; only the migration-role API does.
  → decisions: Runtime processes never run migrations
- **Database time is the only clock.** → decisions: Database time is the only clock

## Testing

Run checks from `services/runtime/` with a separate `UV_PROJECT_ENVIRONMENT`;
commands are in `services/runtime/README.md`.

- **Database tests drop databases and create roles.** Point
  `MARKOS_TEST_ADMIN_DSN` only at a disposable local cluster. **Never at port
  15432 (the running preview) or 5432 (the Mac's own PostgreSQL).** → decisions:
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

A preview runs from
`~/Documents/Codex/2026-11-10/referenced-chatgpt-conversation-this-is-an/work/markos-preview`
(PostgreSQL on 15432). **Never update, reprovision or delete it** without Mark's
instruction. A fresh preview from this repo follows `docs/local-preview.md`.

## Workflow

- **One focused branch and PR per change.** Ship the code, tests and docs
  together. Add a `CHANGELOG.md` entry.
- **Never merge, deploy or push to `main` without Mark's go-ahead.**
- **Lean delivery:** show working product results early; automate regression
  tests rather than asking Mark to run them. → decisions: Lean delivery: runnable
  feedback first, real gates before real data
