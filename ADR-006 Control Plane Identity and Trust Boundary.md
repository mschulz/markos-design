---
title: ADR-006 Control Plane Identity and Trust Boundary
type: architecture-decision
status: proposed
version: 1.1
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - security
---

# ADR-006 Control Plane Identity and Trust Boundary

## Decision and scope

Use one trusted interaction gateway to authenticate humans and delegate narrowly scoped operations to PostgreSQL. Keep human identity separate from agent/service login identity. Implement the versioned synthetic contract profile in [[MarkOS v0.3 M1-A Contracts and Identity]] first; authentication, database authority, API routes, sessions and dispatch remain later slices. This ADR is proposed for review, not production enablement. It follows [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] and [[ADR-005 Governed MCP Access for Agents]] without changing RabbitMQ, PostgreSQL authority, source objects, derived Markdown or rules-first M3 routing.

The owner authorized M1-A after review of PR #18. That M1-A authorization covered contracts, explicit inert configuration, tests and identity decisions. The owner has subsequently authorized M1-B persistence separately; see [[MarkOS v0.3 M1-B Control Plane Persistence]]. M1-C–M1-G, accounts, tools, merging and deployment remain outside that implementation authorization. Passing a `HumanPrincipal` model proves only that a descriptor has the right shape. Neither a principal descriptor, a role snapshot, an intent digest nor successful preflight authenticates a person or confers runtime authority.

## Authentication and session profile

For the first implemented/tested profile, use only a synthetic issuer `https://auth.markos.test`, audience `markos-control-test`, asymmetric ES256 and normalized token kind `human-access`. No key URL, issuer, algorithm or token kind is selected from untrusted input. The checked-in public-key placeholder has an empty key set and is deliberately unusable; M1-A does not read it, fetch keys or accept tokens. M1-C must supply an ephemeral signed-token/PKCE issuer fixture before testing the actual authentication adapter.

Recommend Supabase Auth as the future production identity adapter, subject to a separate configured issuer/project/audience and account/security review. There is no implicit trust from using Supabase PostgreSQL. Reject symmetric/service-role/API-key credentials as human access, unsupported algorithms, invalid signatures, wrong issuer/audience, expired/not-yet-valid tokens, missing subject/session and unknown keys. Translate provider-specific claims into the internal human descriptor only after verification; an internal `token_kind` field is not a requirement that Supabase emits that custom claim automatically.

Freeze the following synthetic implementation requirements now. They are contract/decision evidence, not active middleware:

| Area | Decision for later implementation |
|---|---|
| Human binding | Map verified `(issuer, subject)` to an administratively provisioned principal and current owner/domain memberships; neither a model nor browser chooses an actor/role. No self-provisioned domain access. |
| Keys | Approved configured key source only; 64-KiB key-set ceiling, 60-second cache ceiling, five-second absolute fetch deadline, bounded refresh admission and no token-provided `jku`/`x5u`. Unknown key fails closed after at most one admitted refresh; never repeat unbounded fetches per attacker request. |
| Browser | Same-origin opaque random session; persist only token/session lookup hashes and encrypted refresh material where needed. Cookie `__Host-markos-session`, Secure, HttpOnly, SameSite=Lax, Path=/, no Domain. Rotate on login; validate CSRF plus Origin on writes. Never store provider tokens in browser storage or query strings. |
| Session bounds | 30-minute idle and eight-hour absolute maximum, bounded further by provider/token validity; at most ten active sessions/principal and 100 globally. Renewal verifies current provider/local authority and never extends the original absolute deadline. |
| CLI | Explicit PKCE/browser onboarding to the same verified actor model; five-minute bearer ceiling, no token on command line, in Git or logs. Secure token storage/onboarding are M1-C work, not an existing command. |
| Local revocation | Database-backed session, membership and policy checks at every protected request and dispatch; stream recheck at most every 15 seconds. Logout/revocation blocks the next operation and future retries. A cached role or signed JWT cannot bypass this. |
| Provider revocation | Before real onboarding, the adapter must verify current upstream session validity using supported provider behavior. Freeze a maximum 15-second validity-cache bound; unknown/unavailable authority fails closed. Prove revocation with fixture and provider-specific tests; offline signature verification alone does not provide immediate provider logout. No unsupported assumption about a provider SDK's user lookup is accepted as evidence. |
| Restore | A fresh restored generation invalidates all old browser/CLI sessions, unconsumed approval authority and dispatch leases. Independently current revocation/deletion records must be reapplied before real access; an older snapshot is not current authorization. |

Provider selection for real use, MFA/recovery, network allowlists, credential custody and operational ownership remain production gates. M1-A introduces no production authentication configuration. A future provider unable to satisfy the revocation contract remains disabled.

## Role matrix and database delegation

Roles are additive, scoped and deliberately nonhierarchical. An owner can be assigned multiple explicit roles; administrator does not silently mean approver or operator.

| Capability | Viewer | Operator | Approver | Administrator |
|---|---|---|---|---|
| Inspect scoped metadata/status | Yes | Yes | Yes | Yes |
| Send synthetic chat / permitted operator controls | No | Yes | No | No |
| Decide exact approval intents | No | No | Yes | No |
| Propose/activate reviewed registry/config revisions | No | No | No | Yes |

These are maximum capabilities subject to current object grants, membership, session, policy and version checks. No role can add itself to another owner/domain, approve an agent's own request or override a denied tool grant. Registration/configuration is not broker administration, tool discovery permission or authority to execute code. Permission expansion requires a separately reviewed administrative workflow; changing a config field cannot grant a privilege.

Give the gateway a separate LOGIN role with no superuser, BYPASSRLS, migration, role-creation, base-table-write or broker-management privileges. It may execute only reviewed narrow functions. PostgreSQL sees an authenticated gateway service, not a human token. The gateway is explicitly trusted to supply the verified actor; every database function then rechecks current membership, role, object scope and revision. The existing M0 `session_user` mapping must not be repurposed into an arbitrary per-request owner setting. Caller-controlled `SET`/GUC values and pooled-connection state are not authorization.

This concentrates trust: a compromised gateway holding its database credential can impersonate actors at its delegated function boundary. Narrow functions, minimal returned columns, immutable audit, separate runtime credentials and private network access reduce that exposure; they do not eliminate it. Do not claim RLS independently verifies JWTs through a normal Psycopg service connection. M1-B tests function grants and scope; M1-C tests authentication, forged requests and pool reuse. Agents receive neither gateway login credentials nor human session material.

## Contracts, routing and approval identity

The M1-A contract namespace is separate from the active M0 event union. Future `control.chat.requested` uses producer `control-gateway`, route `v1.control.chat-requested` and intended consumer `control-test-agent`. Future `control.chat.completed` uses producer `control-test-agent`, route `v1.control.chat-completed` and consumer `control-activity`. Both carry owner/domain-bound typed message references and checksums, never full chat text or credentials. The current M0 parser, topology auditor, SQL intent checks and no-op worker continue to reject those types until M1-D deliberately extends them together.

The initial agent revision is disabled, deterministic, has zero retries, no model and no required tools. Configuration revisions are contiguous and scope bound; future state changes carry an expected revision and reason. Human HTTP chat/approval request bodies have no owner/actor/role/token/tool/endpoint fields. Authenticated context must supply those in later slices. Validation of a request to enable an agent is only a proposed state transition, not execution permission.

Approval intent identity binds owner/domain, principal, task/run/epoch/fence, agent and versions, exact fixture server/tool/schema, grant/policy versions, operation key, canonical arguments and preview digests, recipient/effect/data class and expiry. The M1-A fixture contract permits only synthetic `fixture.read`; it enables no grant or client. Maximum intent expiry is 15 minutes and one attempt; broader read retry or real tool effects require a later reviewed contract revision. Canonicalization v1 is sorted-key compact UTF-8 JSON with no NaN/Infinity, normalized model defaults/UTC values and exact strings, not a claim of general RFC 8785 compatibility. The digest is a stable binding, not a signature or authorization token.

The Approve click records a decision; one-time consumption belongs in a committed dispatch transaction after current authority is checked. Waiting releases the execution slot and holds no delivery/transaction. Prior dispatch evidence survives failed result recording; uncertain outcomes are reconciled, never blindly repeated. An approval cannot turn a denied grant into an allowed call. M0 operational review resolution and human effect approval remain distinct typed records.

## Frozen test limits and retention

`ControlLimits` freezes the M1 plan's byte, stream, backlog, session, rate, database and resource profile as exact version-one values. Schema/type validation rejects attempts to change them; it does not enforce cross-process admission, limit a running container or perform cleanup. HTTP 64 KiB, chat text 16 KiB UTF-8, responses 256 KiB, list 100 rows, SSE frame 8 KiB; stream bounds one/session, four/principal, 20 globally, ten-minute connection and 15-second reauthorization. Writes are 30/minute/principal with burst five; pending chat 100 globally/ten per scope, pending approvals 100/ten, unresolved-or-reserved dispatch 100/20. Reserve outcome capacity before dispatch and never evict accepted work. Future database transactions, not process counters, enforce aggregate bounds.

Database statements are five seconds, locks one second and HTTP request work ten seconds; idle streams release database resources. The proposed API process ceiling is 256 MiB/0.5 CPU/pool four, separate from the ADR-005 aggregate tool gateway and two global execution slots. No new container or cloud capacity is applied. M1-A retains ADR-005's proposed MCP 512-MiB/0.5-CPU, two global/one-per-run attempt budget and other tool limits; MCP SDK/protocol locking waits for M1-F, with tools disabled until that review.

Synthetic chat/conversation data has a 30-day floor after all associated work is terminal; unresolved work/holds protect required minimal records. Purge expired/revoked session secret material within 24 hours while retaining only permitted minimal revocation/audit evidence; tokens never enter audit. Keep ADR-005's proposed 365-day minimal audit/approval, seven-day optional sensitive copies and 14-day redacted logs, with a 24-hour unresolved alert and daily scoped cleanup. These are synthetic implementation targets, not an approved personal-data retention policy. Source retention and backup erasure are separate; real data stays disabled pending owner/privacy/operational review. No cleanup implementation is added here.

## Dependency and parser decision

Freeze an **optional** `control` dependency profile in the independent runtime lock: FastAPI 0.162.5 (without standard/CLI/cloud extras), Uvicorn 0.54.0 (without standard extras), PyJWT 2.15.1 with crypto, and Jinja2 3.1.6. Preserve existing base pins. The isolated compatibility probe exercises an in-memory ASGI request, ES256 library signing/verification and wrong-audience rejection, template autoescape and construction of a server configuration without binding a socket. It proves primitive compatibility only, not authentication/session or HTTP security acceptance. The base wheel/image remains usable without these optional packages.

Strict Pydantic UUID/time wire parsing must operate on bounded original JSON bytes via `from_wire()`. Default FastAPI validation of a decoded Python dictionary does not preserve those strict JSON-mode semantics. M1-C must enforce size before buffering/parsing, reject duplicate keys/non-finite JSON and redact errors; it must not weaken the contracts to make framework coercion pass. No API entry point is created by this decision.

## Alternatives and remaining gates

Reject exposing the archived API, trusting user-supplied owner/domain/roles, sharing a Supabase admin/service-role credential with clients, giving every agent its own user gateway, unbounded SDK defaults or treating current ChatGPT/Codex connector permissions as MarkOS grants. A second backend project/lock is unnecessary; keep clear modules inside the independent runtime.

M1-B–M1-G still require database role/functions/migrations, actual signature/key/session and revocation verification, transactional admission/consumption, API/web/CLI, typed chat execution, MCP interoperability and distributed enforcement/restore tests. Real accounts, tools, external effects, provider data, production retention, recovery and deployment remain blocked by their own acceptance and authorization gates. See [[MarkOS v0.3 M1 Control Plane Implementation Plan]].

## Sources

Checked 12 November 2026: [FastAPI](https://pypi.org/project/fastapi/), [Uvicorn](https://pypi.org/project/uvicorn/), [PyJWT](https://pypi.org/project/PyJWT/) and [Jinja2](https://pypi.org/project/Jinja2/) package metadata; [Supabase signing keys](https://supabase.com/docs/guides/auth/signing-keys) and [session semantics](https://supabase.com/docs/guides/auth/sessions). Exact optional versions were resolved from official PyPI JSON and locked independently. The security model, bounds and production gates above are MarkOS decisions, not guarantees supplied by those libraries.


## M1-B persistence boundary

[[MarkOS v0.3 M1-B Control Plane Persistence]] implements explicit gateway-role/session/membership checks, rotation/refresh, versioned registry/configuration and atomic staged conversations/messages/activity through additive migration 0006. The gateway remains trusted to bind a verified provider subject; no token verifier/API is supplied. Held proposals are not publishable until M1-D extends the existing event/outbox boundary. Runtime base-table access remains denied; scoped definer functions are the database delegation boundary. Production/provider/MCP enablement and full audit/retention/restore gates remain open.
