---
title: MarkOS v0.3 M0-A Contracts and Configuration
type: implementation-record
status: implemented-for-review
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - implementation
  - testing
---

# MarkOS v0.3 M0-A Contracts and Configuration

## Result and boundary

M0-A implements the independent project authorized by the owner, following [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] and [[MarkOS v0.3 M0 Implementation Plan]]. It is prepared for PR review, not merged or deployed by this work. The only executable command is validation-only preflight; there is no worker/service loop, database/broker adapter, source reader, provider call or production mode.

`services/runtime/` owns package `markos_runtime`, its pyproject, minimal lock, entry point, tests and build. Root `markos` source, root package/lock, legacy tests, exact snapshots and import manifest are unchanged. No runtime import/dependency on the archive, DuckDB, PARA, Obsidian or note storage exists. GitHub `markos-design` remains canonical; the separate `markos` repository is untouched.

## Independent dependency decision

Python 3.13 is selected for a tested modern standard-library/typing baseline and compatibility with Pydantic, rather than inherited as a product requirement from the archive. The project explicitly supports >=3.13,<3.14 and was tested on 3.13.14. A later interpreter expansion requires its own test evidence.

Pydantic 2.13.4 is the sole direct runtime dependency, pinned in this project's independently resolved lock. The wheel has five production dependencies in total (including transitives). Exact development dependencies are pytest 9.1.1, Ruff 0.16.1, mypy 2.3.0 and jsonschema 4.25.1; uv_build 0.12.1 builds the package. The environment was resolved afresh, not copied from the root lock. Overlapping library versions reflect a tested minimal baseline, not reuse of legacy code or a claim of latest/security-audited releases. The workflow pins uv 0.13.6 and action commits.

Pydantic's [strict JSON validation](https://docs.pydantic.dev/latest/concepts/strict_mode/) deliberately accepts wire UUID/date strings while refusing Python-style coercion; additional checks reject duplicate keys, NaN/Infinity, nil IDs and invalid UTC values. [Python's version policy](https://devguide.python.org/versions/) informs the interpreter choice. No automatic settings/discovery framework is used.

## Fit review of prior work

PR #6 was inspected as candidate material. Retained design ideas: strict no-op envelopes, bounded JSON, UTC/identity validation, explicit config, owner/domain isolation, exact subscriptions and schema drift checks. This PR implements them in a new independent project; it does not import `markos.runtime`, use the root lock or promote PR #7.

Changes from the candidate design include explicit typed opaque source references, an envelope-level operation key shared with task requests, a reviewed policy/subscription manifest, consumer handoff requests that require a trusted publisher identity, a real independent CLI/wheel, fresh-lock installation evidence and scoped CI. The source reference is not a path, URI or access grant. Metadata scope validation does not prove that a source exists or is accessible.

## Contract surface

- `EventEnvelope`: version 1 and `foundation.noop.requested` only; non-nil UUIDs, owner/domain/source, UTC timestamps, explicit root causation null, sensitivity, operation key and exactly one inline no-op payload or scoped checksum/source reference. No arbitrary agent payloads are accepted.
- `from_wire`: strict UTF-8 JSON, duplicate/non-finite rejection and a maximum 65,536 raw bytes. `to_wire` revalidates the final UTF-8 body and size, even after unsafe model-copy construction. Current schema byte limit is an application extension; generic JSON Schema cannot enforce serialized UTF-8 size.
- `TaskRequest`: scoped task/event/consumer/operation identity. Factory authorization checks both the explicitly supplied trusted publisher and consuming policy. This is a request value, not a durable task or proof of commit; future adapters must recheck current permissions, expiry and source access before execution.
- `Manifest`: exact allowlisted policies and subscriptions; no wildcard routing, duplicate identities/subscriptions, missing consumer binding, unsupported event or scope mismatch. One synthetic owner/domain scope per service identity; multi-owner dispatcher grants need a reviewed extension. Queue `markos.{domain}.{service}`, exchange `markos.{domain}.events`, routing key `v1.foundation.noop-requested`; vhosts are `markos-test` and `markos-development`.

The manifest and selected service ID are trusted, operator-reviewed deployment input. An event cannot select its own trusted identity. Real authentication/role mapping, RabbitMQ provisioning/ACLs and PostgreSQL RLS are M0-B/M0-C, not proven by local policy validation.

## Configuration and preflight

`markos-runtime preflight --config <explicit TOML> [--event <explicit JSON>]` validates only. `MARKOS_RUNTIME_CONFIG` is the only environment fallback, and the CLI path wins. Only a `[runtime]` table is accepted. The adjacent manifest uses a basename without traversal; symlink manifests are rejected. Reads are bounded to 262,144 bytes for config/manifest and 65,536 for events. Other legacy environment variables, `.env`, home directories and repository configuration are never discovered.

Only `test`/`development`, publisher/consumer roles and the synthetic event are supported. An optional event preflight validates publisher policy and configured serialized message size. Preflight does not execute, publish, persist, resolve source objects or check whether an event is currently executable; separate expiry checks support future execution boundaries.

Database/broker environment-reference names are syntax-checked and distinct. Their secret values are not read, printed or required for validation-only preflight. A success message does not prove credentials, topology existence, connectivity or broker/database permissions. Invalid documents return status 2 with a stable redacted message. There is no service execution command.

`ProposedLimits` validates a bounded subset of ADR-003's planning profile (resource/flow budgets and retention floors), not applied infrastructure. Internal consistency is checked, including memory envelope/alarm, deadlines, prefetch concurrency and retention. Routine replay remains fixed at 30 days; terminal/dedup retention may be longer than their 30/90-day floors. No cleanup/replay behavior is implemented. Broker/client version, digest, TLS configuration, host disk capacity, quotas and enforcement await M0-B/M0-C and measured acceptance.

## Schemas, examples and checks

Four deterministic JSON schemas in `config/runtime/schemas/` cover event, task, manifest and runtime config. Credential-free TOML/JSON examples are synthetic. `python -m markos_runtime.schemas --output-dir <explicit directory>` checks drift; adding `--write` explicitly regenerates. No directory is discovered automatically. Schema validation covers structure; cross-field scope, duplicate-key, expiry, budget and wire-byte rules require the Python boundary validators too.

The independent GitHub workflow installs only this project, checks its lock/lint/format/strict types/tests/schemas, builds a wheel and installs it outside the checkout with hash-checked locked production dependencies. It has read-only repository permissions and no deployment steps. Local equivalents pass; hosted CI status must be reviewed on the PR rather than assumed.

## Acceptance evidence

- 104 independent tests pass: A01 clean-environment/config/import boundaries; A02 strict/forged/unknown fields, UTF-8 65,536/65,537-byte boundaries and redaction; A03 policy/subscription/schema/example consistency. Source/consumer scope, task identity, UTC/expiry, budgets and drift failures are covered.
- Guarded subprocess preflight runs outside the repository with socket construction/DNS, legacy imports and forbidden legacy-path opens blocked. No implicit config is selected even with legacy environment variables set.
- A wheel built from the source distribution runs in a fresh external environment containing only the runtime and five production dependencies; `markos`, DuckDB and pytest are absent. Synthetic preflight passes there with no root package or lock installed.
- Independent lock check, Ruff, format, strict mypy (14 files), generated-schema check, example preflight and `git diff --check` pass. JSON Schema itself is checked with a separate validator.
- The preserved archive separately passes all 145 regression tests, its lock/Ruff gates and strict mypy (38 files). These are preservation checks, not new-system acceptance.

These are M0-A evidence only. A04–A20 remain unimplemented: no real database transactions/roles, broker confirms/returns/ACLs, persistence, locks, redelivery, scheduling/fencing, source access, retention cleanup or recovery guarantee is claimed. Real cloud/provider access and all deployment remain out of scope.

## Next slice

M0-B defines the durable state/migrations and non-admin role model against isolated PostgreSQL. Reconcile event/task/dispatch uniqueness and trusted identity mapping before storage code. M0-C separately verifies and locks broker/client versions and permissions. No M0-A pass promotes the proposed operating profile into production readiness.

## Follow-up CI review

Review of PR #10 found the initial hosted workflow failed before any jobs: its job-level environment referenced `runner.temp`, which is unavailable in that context. The workflow now selects its isolated environment in a run step through `GITHUB_ENV`, where `RUNNER_TEMP` is available. This changes CI configuration only; subsequent hosted results must be checked separately. PR #9’s ADR-002 correction is consistent with the new architecture.
