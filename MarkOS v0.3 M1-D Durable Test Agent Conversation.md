---
title: MarkOS v0.3 M1-D Durable Test Agent Conversation
type: implementation-record
status: implemented-for-review
version: 1.0
owner: Mark Schulz
created: 2026-11-13
updated: 2026-11-13
reviewed: 2026-11-13
tags:
  - architecture
  - control-plane
  - durability
---

# MarkOS v0.3 M1-D Durable Test Agent Conversation

## Main review and scope

Reviewed main `8311f01b27f9ed79cfbfdc8da79951648d9126c6` after the owner merged PR #21. Its tree exactly matches reviewed M1-C head `e0236f523670196eff71e076f1fa3ee6144cf0f4`; history and existing work are preserved. All six post-merge checks pass: [runtime/API CI](https://github.com/mschulz/markos-design/actions/runs/43320422533) covers 623 tests, optional compatibility and standalone installation; [documentation CI](https://github.com/mschulz/markos-design/actions/runs/43320422669) passes. Review of the gateway's signed-token/session boundary, current permissions, resource bounds, original-JSON parsing, private migration state and disabled execution profile found no blocking issue within its synthetic M1-C scope. Its real-provider, interactive-client, scheduled-cleanup and API process/restore limitations remain explicit.

The owner separately authorized M1-D in [[MarkOS v0.3 M1 Control Plane Implementation Plan]]. This PR adds a durable deterministic conversation using the existing PostgreSQL event/outbox/inbox/task path and RabbitMQ transport. It follows [[ADR-006 Control Plane Identity and Trust Boundary]] without introducing a second bus, archived application dependency, Markdown authority, PARA, Obsidian, real provider or tool. Governed MCP and rules-first M3 remain unchanged. There is no merge or deployment.

## Closed contracts and explicit execution profile

The active wire parser accepts exactly the original EventEnvelope no-op variant or the existing typed ChatEvent request/completion variant. EventEnvelope itself still rejects chat and retains its original payload/SourceReference shape. Chat events contain a scoped MessageReference with conversation/message IDs and checksum; they cannot contain chat text, credentials, arbitrary output objects, tool intent or a cloud SourceReference. Producer, reference kind and scope are checked. Routes are fixed: `v1.control.chat-requested` → `control-test-agent`; `v1.control.chat-completed` → `control-activity`. Task/policy schemas recognize these explicit event types; schedules remain no-op-only.

A separate SyntheticConversationApiConfig requires `conversation_profile=deterministic-conversation-v1`, fixed true synthetic chat execution, fixed false real accounts/tools and the same loopback HTTPS constraints. The original SyntheticApiConfig and M1-A preflight configuration remain execution-disabled. An HTTP configuration/registry change cannot select this server profile or executable code. New conversation-request, admission/status and conversation-profile examples/schemas are checked into GitHub; there are 31 schemas. The optional API/JWT dependencies and base pins are unchanged.

The existing `create_app` factory accepts the separately constructed profile. `ConversationWorker(..., confirm_synthetic=True).run_once()` is a fixed library operation, not a generic executor, process launcher or dynamically loaded agent. It accepts the same explicit in-process FixtureIssuer used by the test API, checks its current session before execution and completion, and uses the shared four identity-operation leases. It receives only non-secret subject/session metadata and the bound message checksum, never a browser/API bearer, provider JWT, refresh credential or message text. The injected fixture is trusted test infrastructure; this is not a production identity-authority service or process isolation boundary. Real provider verification/authority distribution remains a reviewed production gate.

The response is exactly `Synthetic acknowledgement: ` followed by the stored message checksum. It does not interpret or echo the message, call a model/provider, use a filesystem/shell or discover tools. SQL independently checks that fixed output. Normal handler failures/timeouts are terminal with zero handler retries. A process interrupted without a terminal result may reclaim its same durable operation after lease expiry, up to three fenced attempts; that bounded crash recovery is distinct from retrying a failed handler.

## Atomic admission and promotion

POST of a typed human message checks current local session/membership/operator role, enabled agent, expected agent revision and current bound configuration. One PostgreSQL transaction commits the human message, sequence, activity, idempotency receipt, minimal audit, run/delegation metadata, typed request event, exact intended consumer and outbox. HTTP returns 202 with durable IDs and the immutable initial queued receipt, then releases the request. It never waits for a broker confirm or agent result.

Acceptance promises durable storage, not unconditional completion. Session expiry/logout or revoked authority while queued prevents execution and leaves an inspectable terminal failure; a new login does not silently renew an old run. Repeated identical actor/owner/domain/operation keys return that same admission receipt, including after completion; current run status is read separately. Changed input under the key returns 409. Concurrent contenders serialize in PostgreSQL. Failed admission rolls back every record and sequence increment. The existing 100-global/ten-per-scope bound includes held proposals and promoted unfinished runs, across factories/connections. Terminal runs release their proposal/admission reservation without deleting their messages, run history or idempotency evidence. Accepted work is never evicted to admit another request.

Existing M1-B held proposals remain nonpublishable after upgrade. Only a fresh explicitly authenticated submit of the exact proposal may promote it, with current authority, versions and epoch. Migration does not publish old work. A proposal from a restored old epoch cannot be promoted; it needs a newly authorized operation. Registry registration still enables nothing, grants no tool and provisions no identity/topology.

The migration constructs chat envelopes from durable records through private helpers. Runtime publishers cannot submit arbitrary chat wire bytes through the old no-op append function, and gateway/worker roles cannot execute those private constructors. No-op admission excludes the newly provisioned chat consumers, preserving its existing intended subscribers. Runtime service IDs become unique per owner/domain/service rather than globally, allowing separate scoped logins for the same logical chat role. Existing mappings remain valid. Broker vhosts must remain owner-scoped: identical logical queue names are not permission to share one queue between owners. The disposable transport fixture has one owner/vhost; SQL tests independently exercise other owner/domain identities. Production namespace provisioning remains an operational gate.

## Transport, execution and completion

The existing publisher keeps the exact committed event bytes through publishing and handoff, rather than reserializing SQL-generated JSON and changing its checksum. It verifies the parsed event and exact chat consumer intent, audits topology for that event's route, sends mandatory persistent messages, and records publication only after positive confirm/no return. Missing/incorrect bindings leave the original outbox work retryable. The topology auditor permits the separately approved routes to coexist, rejects wildcards/exchange bindings/unknown routes, and still requires the exact subscriber set for the selected route. The additional sanitized broker definitions are administrator-applied examples; startup never creates queues or grants.

Consumers retain authenticated publisher user_id, wire/type/scope checks, manual ack after durable handoff, bounded cancellation and channel recovery. The received raw wire checksum must match the committed record. Request handoff creates one durable inbox/task per intended consumer/operation. The completion subscriber creates only a completed durable activity receipt/result, with no executable agent task or new assistant message. It does not bypass human API permissions.

The typed worker shares the existing **two global execution slots** with no-op work. Claims have an executor UUID, incrementing fence, current epoch and at most a 30-second nonrenewable lease; handler work is bounded at ten seconds. No database transaction, connection or broker delivery spans execution or fixture authority checks. Current principal/session/membership, enabled agent, configuration revision, epoch and referenced message checksum are checked again at claim and completion. Generic no-op claim/finalization/renewal cannot operate on the chat identity. A disabled/revoked/stale/corrupt request produces no reply; its durable terminal reason remains inspectable.

One fenced SQL completion transaction commits exactly one assistant message, ordered conversation sequence, task result, succeeded activity, audit, typed completion event, exact completion consumer/outbox and terminal run/task state. Completion preserves correlation/trace IDs and identifies the request event as causation. A unique reply constraint and epoch/fence/executor/lease checks reject duplicate/late results. Failure at any insertion/update rolls the entire completion back. Losing a response after commit leaves the single durable reply recoverable through the API; retry does not execute it again.

Explicit cancellation is restricted to the original operator in the current scope. It is idempotent, records a terminal state/activity/audit, fences any running task and releases its execution slot. A late result cannot overwrite cancellation. Local authorization changes and restore generation fencing also prevent stale execution/completion. Completed history survives; an old idempotency receipt does not grant fresh authority. Broader current-revocation reapplication and populated API/worker process backup/restore drills remain M1-G work.

## Shared API and reads

The conversation profile adds these routes under `/api/v1`, using the existing authentication, CSRF/Origin, rate, byte, header, response and deadline protections:

| Routes | Behavior |
|---|---|
| GET/POST `/conversations` | Current scoped history or operator-created conversation with idempotency key and explicit UUID |
| GET `/conversations/{id}/messages` | Authorized, ordered human/test-agent messages, bounded to 100 rows/256 KiB |
| POST `/conversations/{id}/messages` | Strict ChatRequest with path/domain binding and durable 202 admission |
| GET `/conversations/{id}/messages/{message_id}/status` | Scoped queued/working/succeeded/failed/cancelled state, fixed reason and durable result IDs |
| POST `/conversations/{id}/messages/{message_id}/cancel` | Original operator's scoped cancellation; no generic execution control |

Requested owner_id/domain scope is verified against current membership, never treated as authority. Missing or inaccessible object IDs within that scope return the same 404; missing scope capability remains 403. Status exposes no delegation session, credentials, hostnames or submitted payload in errors. List cursors are encrypted/authenticated, expire after ten minutes and bind principal, owner, domain and resource; a conversation cursor cannot page messages or another conversation. Reads filter scope before pagination. Browser and CLI protocols use the same API; no direct client SQL/broker path is added. Polling works across reconstructed API/worker objects because all conversation/work/activity state is durable. Actual web/CLI commands, rendering/accessibility, SSE and its restore/reconnect acceptance remain M1-E/G.

## Migration, CI and evidence

Additive `0008_durable_conversation.sql` adds private RLS run state, human/assistant message linkage, exact reply uniqueness, typed promotion/cancellation/claim/completion/authority helpers, scoped service uniqueness and guarded wrappers for existing transport/recovery/no-op operations. It creates no cluster role, login mapping or account. Bootstrap explicitly provisions separate scoped gateway-publisher, chat-worker and completion-consumer runtime logins. They cannot read/write control base tables or private helper functions; the gateway keeps its separate EXECUTE-only boundary. Migrations 0001–0007, root archive/package/lock/import history, base dependency pins and no-op schedules are byte-preserved. API schema fingerprints now require migration 0008; startup never migrates.

Upgrade tests reconstruct a populated M0-only database and a populated M1-C database before applying the new sequence twice. They preserve old wire/checksums, valid sessions, registry/configuration/history, messages and held/idempotency records; neither upgrade creates a chat event automatically. Existing broker, retention, volume-loss, lifecycle and standalone wheel checks remain required. The optional API CI job now requires PostgreSQL **and RabbitMQ**, with actual localhost HTTPS admission, two competing workers, durable completion subscription, wrong-route denial and transport faults. Topic permission fixtures use the [RabbitMQ HTTP API](https://www.rabbitmq.com/docs/http-api-reference); no production broker is configured.

Local validation passes **569 base cases** and **117 optional API/conversation cases**. Twenty broker/dedicated recovery cases require hosted services and are explicitly unpassed locally. Lock, Ruff/format, strict mypy in both installations (78 files), 31 generated schemas, seven documentation-checker tests, 44-document inventory and whitespace checks pass. The expected required matrix is **706 tests**: 582 normal base cases, 122 optional API/conversation/transport cases, one volume-loss and one lifecycle/restore drill. Required service absence fails hosted CI. Local absence of RabbitMQ is recorded as a skip, never passing transport evidence. Covered M1-05/07/10/12 subsets include atomic durable admission, ordered exactly-one reply, current versions/authority, cancellation and fenced takeover; M1-11 faults exercise SQL rollback, real channel redelivery, response loss and abandoned leases. They do not yet prove OS-level kill/restart of packaged conversation services. Full M1-13/14/15/16/19/20/21/22, production authentication, tools/MCP, retention/erasure policy, current backup revocations, API/worker process packaging/resource/proxy enforcement, cloud availability and monitoring remain open. No production-ready or whole-M1-closeout claim is made.

Related: [[MarkOS v0.3 M1-C Authenticated Shared API]], [[MarkOS v0.3 M1-B Control Plane Persistence]], [[MarkOS v0.3 M0 Runbook]] and [[Documentation Authority Index]].
