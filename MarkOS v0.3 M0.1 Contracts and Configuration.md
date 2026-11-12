---
title: MarkOS v0.3 M0.1 Contracts and Configuration
type: implementation-record
status: proposed
version: 1.1
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - implementation
  - contracts
  - configuration
---

# MarkOS v0.3 M0.1 Contracts and Configuration

## Result and review boundary

This records the M0.1 baseline at a1643ea. The subsequent terminology update
uses `markdown_root`; see [[Markdown Root Usage]] for compatibility and new test
evidence. The original M0.1 regression counts below remain historical.

M0.1 prepares strict synthetic event contracts, independent runtime configuration,
publisher/subscription definitions, typed adapter ports and a validation-only
startup preflight. It is a separate review branch stacked on the import-promotion
branch. No merge or deployment is performed. The existing Python 3.13 toolchain,
application modules, CLI entry point, package version and dependency lock remain
unchanged.

[[MarkOS v0.3 M0.0 Review]] verifies PR #4's preserved history and 145-test
baseline. Its integration gap is handled by a separate promotion PR to main;
M0.1 does not silently assume that the import is already on main.

## Implemented boundaries

| Path | Behavior |
|---|---|
| `src/markos/runtime/contracts.py` | Strict frozen event/scope/no-op payload models, bounded wire parser, UTC/expiry/XOR checks and identity validation |
| `runtime/settings.py` | Explicit bounded TOML reader; role, service scope, credential environment references, profile budgets and fixed retention-policy values |
| `runtime/topology.py` | Registered publisher grants, independent owner-scoped consumer queues, source allowlists and conservative aggregate queue/connection/channel budgets |
| `runtime/bootstrap.py` | Read chosen config/manifest, validate environment/role/scope/budgets and secret endpoint syntax; return masked credentials without connecting |
| `runtime/ports.py` | Typed Clock, EventStore, InboxStore, EventPublisher and SourceAccess protocols, with no adapter implementation |
| `runtime/schemas.py` | Explicit `--write`/`--check` JSON Schema artifact command; no import-time export |
| `config/runtime.*` | Three generated Draft 2020-12 schemas, synthetic event example, subscription manifest and credential-free TOML example |
| `tests/runtime/` | Contract, identity, budget, secret/error redaction, schema-drift and preflight isolation tests |

The only supported event type is `foundation.noop.requested` (schema version 1).
Unknown versions/event types, extra fields, wrong primitive types, nil ownership,
naive/non-UTC timestamps, invalid expiry, duplicate JSON keys, non-finite numbers
and invalid UTF-8 are rejected. Causation is explicitly nullable for a root event.
Exactly one non-null payload or payload_ref is required. Inline data is a typed
NoopPayload with a bounded operation_key and label; referenced content needs the
same payload validation when a later SourceAccess implementation resolves it.

Use `EventEnvelope.from_bytes` at the untrusted wire boundary and `to_bytes` before
publication. The complete UTF-8 body is capped at 65,536 bytes, including envelope
overhead, whitespace on input and multi-byte labels. Schemas express structural
constraints and XOR, but the Python parser also enforces byte limits, UTC and
cross-field semantics. JSON Schema alone is not the authorization/wire validator.

## Routing and authorization contract

Events route to `markos.{domain}.events` using `v1.foundation.noop-requested`.
The example has one publisher and separate worker/observer subscriptions. Queue
names include the owner UUID, domain, logical service and version. Replicas of a
logical consumer use its same queue; independent consumers use separate queues.

`TopologyManifest.authorize_publication` requires both trusted owner/domain/source
identity and a declared publisher/event grant. A consumer-only identity cannot
publish. `Subscription.validate_delivery_scope` checks consumer identity,
owner/domain, event type and allowed source. Manifest grants must reference
registered publishers in the same owner/domain, and duplicate definitions fail.

These are configuration/application checks. They do not prove a credential's
identity, implement broker ACLs/RLS, resolve a source reference, or authorize an
external effect. Future adapters must bind trusted deployment identities, enforce
database/broker permissions and call SourceAccess for ownership/access/deletion
checks before using referenced bytes. Tenant identity is never trusted merely
because an event claims it.

## Configuration and startup preflight

`validate_startup` loads only an explicitly supplied Path or MARKOS_RUNTIME_CONFIG.
It does not discover pyproject, `.env`, a home config or legacy MARKOS_CONFIG,
MARKOS_MARKDOWN_ROOT or MARKOS_INDEX_PATH. The only file reads are the selected TOML
(at most 64 KiB) and its named sibling manifest (at most 256 KiB). Traversal and
absolute topology filenames, unknown tables/settings and malformed input fail.

TOML declares a publisher or consumer role and a trusted scope; preflight requires
that role/scope in the matching manifest definitions. It reads credentials only
from the named MARKOS_RUNTIME_* environment variables, never from TOML. Missing
or invalid credentials fail closed with generic errors. Returned SecretStr values
are masked in repr/JSON; callers must not explicitly reveal or log them.

Production syntax requires AMQPS and PostgreSQL sslmode=verify-full. Plaintext is
permitted only for loopback endpoints in test/development. This is syntax/policy
validation, not DNS, TLS certificate or server verification; no connection is
attempted. Broker URL query overrides are rejected; database URLs permit only
a single sslmode option. Reviewed timeouts come from typed limits rather than
URL overrides. M0.3 and database adapters must implement verified transport.

The default profile remains the ADR-003 proposal: 4 CPUs / 8 GiB VM, broker cap
4 GiB with 2 GiB alarm, 10/20 GiB disk alarm/warning, 64 KiB bodies, per-queue
10,000 messages/32 MiB, 64 queues, 32 connections/channels, prefetch 10, two
handoffs/agent slots, 100 in-flight confirms, 10/30-second handoff/total-delivery
budgets and replay 10 events/second in batches of 100. Cross-budget contradictions
fail validation. Limits may be reduced within the validated profile; increasing
bounded transport maxima requires an explicit reviewed code/profile change.

Thirty-day replay and terminal retention and the 90-day deduplication floor are
versioned configuration values only. No cleanup or retention policy is applied.
Each manifest definition declares a conservative connection/channel budget;
preflight checks the totals against limits. No broker provisioning or resource
enforcement occurs in M0.1.

## Tests and reproducibility

The complete suite passes **224 tests**: the original 145 plus 79 M0.1 cases.
Ruff and strict mypy (50 files) pass, and `uv lock --check` passes with unchanged
uv.lock. Tests use synthetic files/identities/endpoints only. No dependency was
added or updated.

Coverage includes malformed/ambiguous JSON, integer-vs-bool strictness, required
ownership, payload/reference exclusivity, exact UTF-8 body boundary, expiry with
an injected clock, forged publication/delivery identities, consumer-to-publisher
escalation, duplicate/unregistered grants, role/environment mismatch, budgets,
missing/invalid secrets and masked representations. A subprocess audit hook
proves preflight can run with malicious synthetic legacy environment values
without loading legacy settings, accessing their files/Markdown root/index or opening
a network connection.

Run the standard gate, then check generated contracts:

```console
uv lock --check
uv run ruff check .
uv run mypy
uv run pytest
uv run python -m markos.runtime.schemas --check
```

Use `--write` explicitly when reviewing model changes; commit regenerated schemas
with the model/tests. Schema export reads no runtime configuration or secrets.

## Acceptance and remaining work

A02 is covered at the M0.1 validation-only startup boundary. A03's model/wire and
scope rejection behavior is covered; durable quarantine, actual credentials,
source-access checks and transactional denial require M0.2/M0.3 tests. No runtime
service loop, markos-runtime CLI, broker, database, schema migration, scheduler,
real source resolver or external effect is implemented. There is no claim of
durability, deployment, operational capacity or completion of v0.3 M0.

## Related

- [[MarkOS v0.3 M0 Implementation Plan]]
- [[MarkOS v0.3 M0.0 Review]]
- [[ADR-003 RabbitMQ Internal Event Bus]]
- [[MarkOS v0.3 Specification]]
- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)
- [Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/)
- [Python bounded TOML loading](https://docs.python.org/3.13/library/tomllib.html)
