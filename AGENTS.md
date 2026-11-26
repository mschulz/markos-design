# AGENTS.md

The project rules are in [CLAUDE.md](CLAUDE.md), and the reasons for them are in
[docs/decisions.md](docs/decisions.md). Read both before changing code.

The rules that matter most:

- All code is in `src/markos_runtime/`. The v0.2 design (PARA, DuckDB, Obsidian,
  Markdown as source of truth) is abandoned.
- Migrations in `infrastructure/migrations/` are append-only. The next is `0023`.
- Database tests drop databases. Point `MARKOS_TEST_ADMIN_DSN` only at a
  disposable local cluster, never at port 15432 or 5432.
- Synthetic data only. Never commit or log secrets or personal data.
- One focused branch and PR per change. Never merge, deploy or update the running
  preview without Mark's go-ahead.
