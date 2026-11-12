---
title: MarkOS v0.3 M1-B Control Plane Persistence
type: implementation-record
status: implemented-for-review
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - control-plane
---

# MarkOS v0.3 M1-B Control Plane Persistence

## Merged-main review and scope

Reviewed main at `ea076ff39f72dab8ec5650d74d6c34fe195d7aa0` after owner-authorized merges of PRs #18 and #19. Its tree matches the reviewed M1-A head `32e4337`; prerequisite commits/history are preserved. All five post-merge checks pass: [runtime CI](https://github.com/mschulz/markos-design/actions/runs/43188993429) covers 469 normal cases plus volume-loss and lifecycle/restore (471 total) and optional stack compatibility; [documentation CI](https://github.com/mschulz/markos-design/actions/runs/43188993495) passes. No blocking correctness/architecture finding was identified within M1-A's contract-only scope. Its descriptors, role matrix and digests remain data, not JWT verification or authority by themselves.

The owner authorized M1-B persistence through a separate PR. This implementation follows [[MarkOS v0.3 M1 Control Plane Implementation Plan]] and [[ADR-006 Control Plane Identity and Trust Boundary]]. It adds explicit PostgreSQL library operations and additive migration 0006. It does not add an HTTP API, login/token verifier, browser/CLI gateway, live account, chat publisher/executor, approval dispatcher or MCP client. RabbitMQ remains the internal bus; PostgreSQL owns state; no PARA, Obsidian, Markdown authority or archived application dependency is introduced. Governed MCP and rules-first M3 decisions are unchanged.

## Schema, bootstrap and access boundary

`0006_control_plane.sql` creates a separate `markos_control` schema with principals, memberships, hashed session bindings, agent/configuration current state and immutable histories, conversations/messages, held message intents, scoped sequence counters, activity, idempotency and minimal successful-operation audit. Messages store a database-checked SHA-256 of exact UTF-8 content. Migrations 0001–0005 and their checksums remain unchanged; schema ledger now records six migrations.

Privileged bootstrap must create the **NOLOGIN** `markos_control_gateway` group before migration. The migration role needs no CREATEROLE privilege and creates no login/account. A separately provisioned non-admin gateway login must both belong to that group and be explicitly listed in private `gateway_logins`. It must not be a superuser, BYPASSRLS/CREATEROLE/CREATEDB role, schema-owner member or M0 runtime-client member. Tests provision synthetic roles explicitly; no real gateway credential or login mapping is installed by this work.

The group has schema usage and EXECUTE on narrow public functions only. It has no base-table/sequence access or private-helper execution, membership/provisioning endpoint, migration or M0 restore authority. All tables enable RLS without gateway policies; qualified SECURITY DEFINER functions implement scoped access under the schema owner's privileges. Function grants and search paths are explicit; temporary tables and caller-controlled settings cannot replace authority. Runtime/agent credentials remain separate. The gateway is still a trusted delegation service: possession of its credential can impersonate a provisioned subject through its session-binding function. This does not make PostgreSQL a JWT verifier; M1-C must authenticate before calling these functions.

## Sessions and current authority

`ControlStore.connect(dsn)` is explicit, non-admin and bounded to four pool connections, five-second statements and one-second locks. It resolves no secret reference and is never called by preflight or worker startup. Typed methods expose session persistence, registry/configuration, conversation/message staging and scoped reads; there is no arbitrary public SQL interface. Failures use fixed denied/conflict/limited/unavailable errors without SQL, credentials or payloads. Cancelled transactions close their connection rather than return an uncertain connection to the pool.

A trusted future verifier supplies an approved synthetic issuer/subject, provider-session ID, lookup hash and provider expiry to `open_session`. Only previously provisioned enabled principals can obtain a binding. No raw browser secret, JWT, refresh token or provider key is stored. Session contexts contain a private hash whose representation is redacted. Each operation resolves the bound principal from the session, verifies lookup hash, local revocation, current generation, provider/absolute/idle expiry, enabled principal and current owner/domain membership/role. Missing generation authority fails closed. The future verifier remains responsible for actual token signatures, upstream session validity and PKCE.

Browser sessions have a 30-minute idle and eight-hour absolute ceiling; CLI sessions have a five-minute ceiling, both bounded by provider expiry. Database checks enforce ten active sessions/principal and 100 globally. Rotation revokes the old binding and creates the new one atomically; a failed rotation restores the original binding by rollback. Refresh is browser-only, requires a still-valid binding and cannot extend the original eight-hour absolute deadline. Logout is explicit/idempotent; it does not revoke a provider session. Old generations and expired/revoked bindings do not consume active-session capacity.

Authority checks/mutations share the existing restore advisory lock and acquire row locks on gateway mapping, session, principal and membership. Pending revocation is observed before a new operation is admitted; an already-authorized transaction can commit before a later revocation. This is a bounded synthetic serialization policy, not measured shared-VM capacity. Every request/dispatch must still reauthorize in its later slice; no connection setting chooses an owner. Sessions have no request-scoped identity state left in pooled connections.

## Registry, configuration and history

Only the explicit administrator capability can change registry/configuration. Roles remain nonhierarchical: administrator does not imply chat or approval capability. The fixed deterministic `control-test-agent` starts disabled, with no tools/model/retries. SQL independently validates manifest/config shape, scope, constants, revisions and current referenced agent, in addition to Python contract validation. No registration function declares a queue, changes membership or grants a tool.

Each agent change, including enable/disable, consumes one expected revision, updates the manifest's revision and appends history/audit. A new manifest revision resets enabled state to false. Configuration revisions are contiguous, use expected previous revision and bind the current exact agent manifest. Therefore a state/revision change requires configuration to be reviewed against the new agent revision before new staging can proceed. Concurrent writers have one winner; stale versions fail with a conflict. History/audit are immutable to runtime identities, while the migration/operational owner retains privileged maintenance authority. Audit contains fixed operation metadata and IDs, never message bodies or session hashes; this is not the complete M1-F approval/dispatch audit system.

## Conversations, held work and atomicity

An operator can create an owner/domain conversation and stage a synthetic human message. Current inspect capability permits scoped shared history; this is owner/domain isolation, not per-conversation private ACLs. Conversation sequence numbers are allocated in the database, so concurrent messages have a unique order independent of timestamps. Idempotency keys bind actor/owner/domain/operation kind and database-computed request content. Identical retries return the original IDs; changed content conflicts. Existing receipt lookup still requires current human scope/capability, but does not create new work if an agent has since been disabled.

Staging atomically writes the message/checksum, a **held** message intent with reserved event ID and explicit intended consumer, queued activity, idempotency receipt and audit. It records the current agent/config revision and epoch. Shared transactions enforce at most ten held intents per owner/domain and 100 globally, across connections/sessions. Failed writes roll back sequence increments and all records; accepted work is never evicted to make room.

A held intent is a nonpublishable proposal, not the M0 event/outbox. No M0 event, intended-consumer dispatch, broker queue, runnable task or assistant result is created. The staging receipt is a library result, not an HTTP 202 or delivery promise. This is the persistence portion of M1-05. **M1-D must atomically promote a still-authorized proposal to the reviewed event/consumer/outbox contract after extending M0's parser, SQL, topology and typed executor together.** Old-generation, disabled-agent or stale-configuration proposals require renewed authority; a restore must not blindly promote them. This avoids widening the active M0 no-op-only path in M1-B or introducing a second event bus.

Reads filter owner/domain before pagination. Conversations/activity use scope-specific counters; messages use per-conversation sequences. Pages have at most 100 rows, and message pages are conservatively capped at 256 KiB serialized JSON. Callers resume from the last returned sequence. Opaque cursor signing, SSE, transport errors/headers and UI privacy remain M1-C/E work. No public metrics/count endpoint or secrets view is added.

## Acceptance evidence

The new suite contains **61 real PostgreSQL cases**. Required acceptance totals **532 pytest cases**: 346 unit/boundary, 171 PostgreSQL, 13 broker/database, one volume-loss and one lifecycle/restore drill; optional control-profile compatibility is additional evidence. Existing schema/lock/format/type/docs gates remain required. Local absence of RabbitMQ is explicit; hosted CI must execute its integration cases.

| Case | M1-B evidence and limitation |
|---|---|
| M1-04 | Direct gateway/runtime/unmapped/privileged access, private-helper/restore execution, base-table reads/writes and history edits are denied. Allowlisted gateway role is distinct from M0 service roles. Raw SQL cannot grant tools, forge message actor or broaden manifest scope. |
| M1-05 | Concurrent identical requests create one staged message/intent/activity; changed content conflicts. Injected failures at intent/activity/idempotency/audit writes roll back all state and sequence. Actual event/outbox/delivery atomicity remains M1-D acceptance. |
| M1-06 | Disabled registration, explicit state revisions, immutable histories, exact configuration references and competing updates have expected-version evidence. Disable/stale configuration blocks new staging; no runtime execution is enabled. |
| M1-07 | Concurrent message ordering, actor-scoped keys, cross-owner/domain queries, pooled reuse, bounded pages and scope filtering pass. Public HTTP cursors/streaming are not implemented. |
| M1-03/18 subsets | Current session/membership/gateway revocation, malformed authority, idle/absolute/provider expiry, hash mismatch, rotation/refresh, missing epoch, per-principal/global session caps and cross-session last-slot/global work admission are tested. Actual signature/upstream-session and distributed HTTP rate/stream limits remain later work. |
| M1-20 migration subset | Upgrade a populated five-migration M0 state to 0006 twice; preserve events and checksum ledger, reject unauthorized/failed migration. Existing M0 generation rotation rejects old M0 claims and M1 session contexts while preserving control history. This is not a new populated control-plane backup/restore drill; M1-G still owes that evidence. |

The established M0 lifecycle CI now bootstraps the new NOLOGIN group and expects six migrations; its assertions/timeouts are unchanged. Control schema creation/restoration is exercised there with empty control data. No required test is replaced by a fake or silently skipped in CI. SQL function/locking choices follow [PostgreSQL 17 definer-function guidance](https://www.postgresql.org/docs/17/sql-createfunction.html) and [explicit locking](https://www.postgresql.org/docs/17/explicit-locking.html), checked 12 November 2026; actual fixture tests establish the behavior claimed here.

## Remaining decisions and next slice

M1-C must implement the authenticated shared API/issuer fixture, token/key/session verification, bounded key refresh, upstream validity, CSRF/cookie security and human-context delegation before any request can call this persistence library. M1-D supplies actual event/outbox promotion and deterministic replies. M1-E/F/G supply web/CLI/SSE, approvals/dispatch/reconciliation, full audit/retention, rate/control-request budgets and populated restore/operational closeout. Session hash purging, pending-intent cancellation/revalidation, conversation cleanup and independent revocation/deletion reapplication are still explicit gates; M1-B stores no refresh secrets and runs no cleanup daemon.

Migrations are explicit privileged operations, never startup behavior. Provision the group before applying 0006, retain a verified backup and review compatibility before any future rollout. The old M1-A runtime can still use unchanged M0 APIs against the additive schema; its old migration directory must not be used to migrate a database with ledger version six. No down migration/drop of control data is proposed. Future restore must fence the restored generation before new sessions and reapply independently current revocations/deletions before real access.

Supabase Auth remains a production candidate, provider/object retention and encrypted off-host restore/monitoring/VM capacity remain unverified, and real MCP/provider/effect access remains disabled. The main architecture, source history, archived application, root package/lock, exact snapshots/import manifest, existing dependency versions and 26 M1-A schemas are preserved. Production readiness stays false. This work merges and deploys nothing.
