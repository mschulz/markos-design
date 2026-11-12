---
title: ADR-0002 GitHub as Canonical Project Record
type: architecture-decision
status: accepted
version: 1.1
owner: Mark Schulz
created: 2026-11-10
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - governance
---

# ADR-0002 GitHub as Canonical Project Record

## Decision

GitHub is the canonical project record for all MarkOS code, tests, infrastructure definitions, configuration schemas and examples, specifications, architecture decisions, roadmaps and documentation. Use the existing `markos-design` repository for code and documentation together going forward. ChatGPT Space supports collaborative drafting and review; approved changes enter GitHub through pull requests.

This records the project owner's accepted governance decision from the [Agent PubSub Architecture conversation](chatgpt-conversation://6aefc392-d95c-83ec-b848-4a884bfa3e7c), reaffirmed in the repository handoff on 10 November 2026. This pull request proposes its repository representation and remains subject to owner review.

## Motivation

A versioned project record keeps design, implementation and verification changes reviewable together. A draft in Space must not silently override a repository decision, and a repository commit containing a draft must not silently convert it into an accepted design.

## Canonical scope

GitHub holds project artifacts. Runtime email, bills, attachments, personal knowledge, databases, credentials, tokens and live secrets do not belong in the repository. Runtime authority (PostgreSQL structured/durable state and immutable source-object storage) is distinct from project source control. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] supersedes the old Markdown-canonical/PARA design; generated Markdown is a view/export.

The separate `markos` repository remains intact. Its v0.2 history was imported into `markos-design` through PR #5 as archived work. That preservation does not authorize reuse as the new foundation. Build the independent runtime in `markos-design`; neither deletion nor pushing a second active implementation into `markos` is part of this correction.

## Change and approval workflow

1. Draft and discuss in Space or a working branch.
2. Reconcile against GitHub, identify conflicts, and preserve source references.
3. Open a focused `codex/` branch pull request describing behavior or decisions, validation and unresolved questions.
4. Obtain owner review before merging. Do not merge this handoff pull request automatically.
5. Reflect the merged decision in Space when separately requested; a Space draft remains collaborative material.

Every implementation milestone includes updated documentation and automated tests in its GitHub change. Each proposed specification keeps its own status and milestone approval gates. Publication of documentation is not deployment authorization.

## Repository organization

Keep existing Title Case documents flat at the root and retain `Templates/`. Add only directories with a concrete purpose: `config/`, `infrastructure/` and `.github/`, plus proposed independent `services/runtime/`. Existing `src/markos/` and root tests preserve the archive. Boundary READMEs distinguish historical implementation from planned services. Defer `docs/` and category folders until a reviewed move plan addresses links and history.

## Alternatives considered

- Space as the authoritative specification: rejected because code review and the versioned project record would diverge.
- Continue separate design and implementation repositories indefinitely: superseded by the owner's decision to keep them together initially; the archived implementation does not become the new foundation.
- Move all existing documents immediately into category folders: deferred because the flat layout is established and moving them adds churn without helping this increment.

## Consequences

### Benefits

Design and implementation can evolve in reviewable increments with durable history. Existing documents and implementation work remain recoverable.

### Costs

Drafts need deliberate reconciliation and status tracking. The separate legacy repository and imported archive require clear authority labels. GitHub access and review are required to adopt changes.

## Related

- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 Roadmap]]
- [[MarkOS Specification]]
- [[Vision]]
