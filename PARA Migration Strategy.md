---
title: PARA Migration Strategy
type: architecture
status: accepted
version: 1.0
owner: Mark Schulz
created: 2026-08-12
updated: 2026-08-12
reviewed: 2026-08-12
tags:
  - architecture
  - para
  - migration
---

# PARA Migration Strategy

## Decision

Existing vault collections will be assessed in place with a read-only migration
audit before controlled writes are enabled. The inbox is reserved for newly
captured or genuinely unclassified items; it is not a staging destination for
large legacy collections.

The audit produces evidence and proposals, not file changes. A later migration
planner will generate complete move, folder-creation and managed-link diffs for
explicit approval.

## Audit Tool

The pre-M11 `markos para-audit` command inventories selected vault-relative
folders using read-only filesystem and DuckDB graph queries.

It reports:

- Markdown and non-Markdown file counts and total size;
- existing `raw/`, `sources/` and `knowledge/` structure;
- nested Git repositories;
- outgoing, unresolved, internal and external inbound links;
- a candidate PARA destination;
- migration risk and recommended migration strategy; and
- a preview of notes whose links may be affected.

Reports may be printed or saved outside the vault. MarkOS rejects report paths
inside the vault.

## Initial Audit Findings

The first audit was run on 2026-08-12 against a current index. No vault files or
folders were modified.

| Source | Markdown | Non-Markdown | External inbound links | Initial treatment |
|---|---:|---:|---:|---|
| Localization Research | 48 | 288 | 0 | Adopt as `Resources/Localization Research` |
| Remote Laboratories | 0 | 1 | 0 | Pilot `Resources/Remote Laboratories` |
| Synthetic Biology | 1 | 1 | 0 | Pilot `Resources/Synthetic Biology` |
| Roam | 3,427 | 180 | 6,332 | Batch migration in place to multiple PARA destinations |

### Localization Research

This collection already contains `raw/` and `knowledge/`, 84 PDFs, operational
outputs and a nested Git repository. It resembles the desired future concept
layout and should be adopted and normalized rather than moved through inbox.

Before migration, decide whether the nested repository and generated caches,
graphs, manifests and outputs belong inside the Obsidian vault. The migration
planner must distinguish source knowledge from rebuildable operational artifacts.

### Remote Laboratories

This is a low-risk pilot containing one PDF under `raw/`. Move the concept folder
under `Resources/`, generate a grounded source note under `sources/`, and create
`knowledge/` only when cross-source synthesis exists.

### Synthetic Biology

This is a low-risk pilot containing a PDF and one Markdown file under `raw/`.
Review whether the Markdown file is extracted/source material before placing it
under `sources/`; raw folders normally retain immutable original artifacts.

### Roam

The Roam export is a large legacy corpus with thousands of notes, unresolved
links and extensive inbound references from the rest of the vault. Moving it
wholesale into inbox would:

- obscure its existing provenance and structure;
- generate unnecessary path churn;
- overload the new-item review workflow;
- make link repair harder to reason about; and
- encourage one false PARA classification for a multi-topic collection.

Roam remains in place while MarkOS audits and migrates reviewed batches to
Projects, Areas, Resources or Archives. Each batch must include a move plan,
reciprocal-link plan, ambiguity report and rollback record.

## Migration Order

1. Remote Laboratories as the smallest raw-document pilot.
2. Synthetic Biology as a raw-plus-Markdown pilot.
3. Localization Research as an existing structured concept adoption.
4. Roam as a separate, incremental legacy-migration program.

Each pilot must complete preview, hash validation, apply, index rebuild, link
health check and rollback verification before the next class of migration begins.

## Backlink Strategy

Moving files can affect explicit path links even when ordinary Obsidian backlinks
remain discoverable. Migration planning therefore separates:

- virtual backlinks already available from the index;
- outgoing concept links proposed for moved or generated notes;
- reciprocal links proposed for existing notes; and
- human-controlled links that are reported but never silently rewritten.

Managed reciprocal sections are idempotent and authorship-neutral. Human- and
AI-authored notes receive the same concept-link analysis, but MarkOS edits only
explicitly managed regions after approval.

## Next Tool Boundary

The audit command remains read-only. The next implementation step is a migration
planner that converts accepted audit findings into a multi-file proposal without
applying it. Actual writes belong to M11's controlled write boundary.

## Related

- [[Milestones]]
- [[Markdown Memory Architecture]]
- [[Mac App Architecture]]
