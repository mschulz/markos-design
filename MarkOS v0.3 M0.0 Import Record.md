---
title: MarkOS v0.3 M0.0 Import Record
type: implementation-record
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - implementation
  - migration
  - validation
---

> **Historical evidence, not the new implementation baseline.** This record preserves earlier work/results. It does not authorize PARA, Markdown/DuckDB authority or reuse of the imported application. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] supersedes those assumptions; see [[Documentation Authority Index]].


# MarkOS v0.3 M0.0 Import Record

## Result and review status

M0.0 imports the existing markos v0.2 application, tests and development metadata
into this review branch while preserving both Git histories. It does not implement
the v0.3 event/runtime foundation. Owner review and merge remain pending; neither
main branch nor the original source repository is changed by preparing this PR.

The owner authorized M0.0 import and regression checks on 12 November 2026.
M0.1–M0.7, deployment, cloud provisioning, real-vault use and external provider
calls remain outside this increment.

## Provenance and history

- Source: [mschulz/markos](https://github.com/mschulz/markos), main `d5d46cbb231e95176a7d052553c176ebe8c805bb`,
  with 27 reachable commits at inspection. It had one branch and no open PRs.
- Design/plan base: `2d6e181c5d109bb4275f5217d3a9187093a3bf35`, the head of open [PR #3](https://github.com/mschulz/markos-design/pull/3).
- Review branch: `codex/m00-history-preserving-import`, stacked on
  `codex/rabbitmq-single-vm-profile` so PR #3 need not be merged to review M0.0.
  After #3 merges, retarget this PR to main and recheck the diff; do not merge
  the stacked import first or squash away its source-parent history.
- Import method: local `git merge --allow-unrelated-histories --no-commit --no-ff`
  followed by deliberate conflict resolution. The import commit's first parent
  is the design base above and second parent is the unchanged source main above.
  No source commit is squashed, rewritten or recreated.
- The source `v0.2.0` tag stays in its original repository. No release tag is
  created or moved in markos-design by this import.

Only fresh temporary checkouts outside synced Documents were used. No existing
user checkout, dirty file, personal config or Obsidian vault was opened or moved.
Remote heads/branches/open PRs were inspected before import; local unpushed work
elsewhere was not claimed to have been audited.

## File mapping and conflict resolutions

The [machine-readable manifest](config/m00-import-manifest.json) records all
46 source blobs, their destination paths, Git blob hashes and SHA-256
checksums. 44 preserve bytes exactly; the two reviewed exceptions are
`.gitignore` and `pyproject.toml`.

| Files | Resolution |
|---|---|
| 21 existing `src/markos/` files | Imported byte-for-byte; no application behavior changes |
| 17 original test files | Imported byte-for-byte; all original tests retained |
| `uv.lock`, `.python-version`, `.envrc` | Unchanged; Python 3.13 and existing locked dependencies retained |
| README add/add conflict | Keep canonical design README; preserve exact source README as [[MarkOS v0.2 User Guide]]; add current development guidance |
| AGENTS add/add conflict | Keep/reconcile canonical project instructions; preserve exact source AGENTS as historical [[MarkOS v0.2 Project Memory]] |
| `.gitignore` add/add conflict | Union of design and source rules; keep Python/env files ignored |
| `pyproject.toml` | Retain package 0.2.0, scripts, dependencies, tooling and Python requirement; point package readme to the preserved guide; remove active machine-specific vault/index paths |
| Existing CLAUDE pointer | Identical in both repositories; retained unchanged |
| Scaffold source/test READMEs | Update to describe actual imported code and distinguish future runtime tests |
| Plan/roadmap/changelog | Record M0.0 preparation and evidence without claiming v0.3 runtime completion |

The manifest also verifies 16 untouched design files,
including Vision, accepted ADRs, current specification, knowledge-memory
architecture, evaluations and templates. No design document is deleted or moved.
The full original configuration remains recoverable in source Git history;
active package defaults select neither vault nor index.

Lockfile source/import blob: `bd5f1de15151d12311191b285b18b791fbb809cb`. No dependency version was changed or
RabbitMQ/PostgreSQL package added. `config/legacy.example.toml` contains synthetic
relative placeholders only; it is not loaded automatically.

## Regression evidence

Checks ran on macOS arm64 using CPython 3.13.14 and uv 0.13.6, in separate
source and import virtual environments. The reviewed `.envrc` was not sourced
automatically. MARKOS_CONFIG, MARKOS_VAULT_PATH and MARKOS_INDEX_PATH were unset
for each gate; test-created temporary fixtures supplied all data. Dependencies
were installed with `uv sync --locked`; subsequent gates ran offline.

| Gate | Original source | Import branch |
|---|---|---|
| `uv lock --check --offline` | Pass, 47 resolved packages | Pass, same lock |
| `uv run --offline --no-sync ruff check .` | Pass | Pass |
| `uv run --offline --no-sync mypy` | Pass, 37 checked files | Pass, 38 checked files |
| `uv run --offline --no-sync pytest -q` | 143 passed | 145 passed |

Existing tests cover read-only HTTP methods, temporary-vault/index behavior,
byte-identical current sync, failed rebuild/concurrency, deterministic watcher
timing, provenance and mocked extraction providers. No live provider CLI or
Gmail account was used.

The two new `tests/test_import_regression.py` cases prove:

1. Active project configuration selects no personal vault/index and still fails
   closed when a vault is required but unspecified.
2. Importing CLI/API with synthetic legacy environment settings cannot read the
   configured file/vault, create an index or connect to a network. A subprocess
   audit hook and settings-load trap enforce this; synthetic note bytes remain
   unchanged.

This closes the import/config part of A01. A02 requires an actual runtime
entry point in M0.1; no runtime startup is invented merely to test it in M0.0.
All event-bus/scheduler/database acceptance cases remain future work.

## Preservation verification and review limits

Before publishing, verify both pinned parents are ancestors of the import commit,
the original source has no missing reachable commits, all original source blobs
are accounted for in the manifest, and every non-exception source blob matches.
Verify untouched design hashes, no conflict markers, clean worktree, and unchanged
remote source/main and design/main heads. Keep the import's two-parent commit
when reviewing/merging; a squash would discard the intended ancestry.

M0.0 is a local regression/import result, not production capacity, cloud runtime,
M0 completion or deployment approval. No CI workflow is added here; M0.7 owns
the runtime CI work. Existing upstream semantic-extraction limitations and the
older knowledge-authority/desktop decisions remain unchanged and unresolved.

## Related

- [[MarkOS v0.3 M0 Implementation Plan]]
- [[MarkOS v0.3 Roadmap]]
- [[ADR-0002 GitHub as Canonical Project Record]]
- [[MarkOS v0.2 User Guide]]
- [[MarkOS v0.2 Project Memory]]
