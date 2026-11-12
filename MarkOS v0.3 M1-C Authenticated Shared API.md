---
title: MarkOS v0.3 M1-C Authenticated Shared API
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
  - security
---

# MarkOS v0.3 M1-C Authenticated Shared API

## Merged-main review and scope

Reviewed main at `6a87fb002d6709675c5d076330d9c3420663a0b7` after the owner merged PR #20. Its tree exactly matches reviewed M1-B head `7c0c1c5bb7153c38b6b07b6df99987f30905ce3a`; the parent history and existing work are preserved. All five post-merge checks pass: [runtime CI](https://github.com/mschulz/markos-design/actions/runs/43202205179) covers 533 cases including broker, volume-loss and lifecycle/restore, plus optional dependency compatibility; [documentation CI](https://github.com/mschulz/markos-design/actions/runs/43202205583) passes. Review of migration 0006, gateway grants, session/membership checks, transactions, configuration revisions and held-message isolation found no new blocking issue within M1-B's persistence scope. Its trusted session-binding functions intentionally require a separate verifier; they are not public authentication endpoints.

The owner authorized M1-C separately. This slice implements the synthetic authenticated HTTP boundary in [[MarkOS v0.3 M1 Control Plane Implementation Plan]], following [[ADR-006 Control Plane Identity and Trust Boundary]]. It adds an explicit optional ASGI factory, a local signed-token/PKCE fixture, browser and CLI session protocols, registry/configuration/status/activity operations, private health and additive migration 0007. It neither exposes the archived application nor promotes held chat work. PostgreSQL remains authoritative and RabbitMQ remains the internal bus. Governed MCP, source objects, derived Markdown and rules-first M3 decisions are preserved.

## Explicit fixture boundary

`markos_runtime.api.application.create_app(config, store, issuer, session_key=..., confirm_synthetic=True)` requires an already explicitly opened ControlStore, a FixtureIssuer instance, an explicit 32-byte key and a validated SyntheticApiConfig. The separate example is `config/runtime/control/api.example.json`; its schema is `control-api.schema.json`. It permits only test mode, synthetic PKCE and a specific loopback HTTPS origin with an unprivileged port. Real accounts, chat execution and tools are fixed false. M1-A's ControlConfig/preflight remains inert and unchanged. Importing the API opens no connection, listener, account or personal folder; optional libraries stay outside base runtime installation.

The issuer is an injected **in-process synthetic authority**, not a Supabase implementation or an externally hosted authorization server. Fixture credentials and signing keys are ephemeral test values. Tests simulate a person authenticating at that authority by calling FixtureIssuer.authorize with a fixture credential, PKCE challenge and nonce; only the resulting one-use code enters the HTTP callback/exchange. No subject, owner or role from the HTTP login request selects an identity. Public signing keys and current fixture session validity are obtained through that injected authority, without network URLs or provider SDKs. There is no discovery, redirect following, real account onboarding, fixture-provider web UI, installed CLI command or credential-file writer. Interactive web/CLI clients belong to M1-E; a real provider adapter must separately satisfy revocation, transport, credential-custody and account-recovery gates. The generated authorization URL is a logical fixture instruction for the harness, not a reachable hosted login page.

This PR starts no long-running service and provides no API launcher or production process configuration. The HTTPS acceptance test explicitly creates and tears down a localhost TLS listener with a temporary certificate and no access log. M1-G still owes packaged API lifecycle, proxy/network policy, process recovery, container resource enforcement and combined control-plane restore drills. Existing M0 lifecycle remains available and unchanged.

## Verification, sessions and secrets

The verifier fixes issuer `https://auth.markos.test`, audience `markos-control-test`, ES256, public EC/P-256 signing keys and token kind `human-access`. It rejects noncanonical token structure, duplicate/nonfinite JSON, unknown headers/claims, token-supplied URLs/private keys, wrong signature/issuer/audience/type, missing subject/session/nonce, noninteger times, expiry/not-before and lifetimes above five minutes. The current upstream fixture session is checked on **every protected request**, exceeding the ADR's maximum 15-second freshness requirement. Unknown or unavailable authority fails closed. Every subsequent SQL operation rechecks current local session, principal, memberships, scope and revisions; no cached role or pooled connection setting is authority.

Browser login stores a five-minute single-use state/binding/nonce flow in PostgreSQL. A Secure/HttpOnly/SameSite=Lax `__Host-markos-flow` cookie binds the callback to its initiating browser; the server's PKCE verifier is encrypted. A successful exchange binds the verified provider subject/session and installs a fresh opaque `__Host-markos-session` cookie with Secure, HttpOnly, SameSite=Lax, Path=/ and no Domain. Old-cookie revocation and new session/credential insertion share one transaction. Rollback leaves the old valid binding usable while the attempted flow/code stays spent; retry starts a fresh flow. A verified new login may switch accounts explicitly, without carrying old authority forward.

CLI onboarding supplies its own S256 challenge and later exchanges the one-use code, matching verifier, state and random flow-binding secret through the same HTTP API. Its returned opaque bearer lasts at most five minutes; it is never a provider JWT. The synthetic client keeps it only in memory. Browser cookies and CLI bearers are channel-bound; ambiguous or duplicate credentials are rejected. There is no CLI direct SQL/broker route. Secure persistent client storage and interactive browser handoff are unimplemented M1-E/provider work.

Only a purpose-separated HMAC lookup of the random API secret is stored, alongside its session UUID. Provider access/refresh tokens and browser PKCE verifiers are Fernet encrypted under a separate HKDF-derived key. The supplied root key stays outside configuration files, Git, responses and logs; production key storage, rotation and recovery remain unresolved. Browser/CLI clients never receive provider access/refresh tokens. CSRF proof is derived separately and returned only by the authenticated same-origin `/api/v1/me` response. Cookie writes require both that proof and exact Origin. Local logout needs valid local possession/CSRF but no upstream lookup, so provider revocation/outage cannot prevent revoking a still-valid local binding.

Existing ten-per-principal/100-global sessions, 30-minute browser idle and eight-hour absolute ceilings remain, bounded further by provider expiry. Refresh verifies current authority, consumes one refresh credential and preserves the original browser absolute deadline. Refresh before provider access expiry; an expired binding requires new sign-in. Epoch changes invalidate old sessions, flows, key-cache publications and admission leases. This is generation fencing, not a claim that an old backup contains current revocation records.

## Shared routes and authority

| Routes | Behavior |
|---|---|
| POST `/auth/login`, GET `/auth/callback` | Same-origin browser fixture flow and opaque cookie rotation |
| POST `/auth/cli/start`, POST `/auth/cli/exchange` | PKCE fixture protocol and five-minute opaque bearer |
| GET `/api/v1/me` | Current principal/memberships; browser CSRF proof; no provider credential |
| GET/POST `/api/v1/agents` | Scoped registry read or administrator revision-one registration; registration is disabled by default |
| POST `/api/v1/agents/control-test-agent/revisions` and `/state` | Current administrator plus expected revision; state endpoint requires explicit owner/domain query scope |
| GET `/api/v1/configuration`, POST `/api/v1/configuration` or `/revisions` | Current scoped configuration and contiguous administrator revisions |
| GET `/api/v1/status` | Authorized scope's latest service/component observations, with fresh/degraded/stale or unknown; no host/credential details |
| GET `/api/v1/activity` | Scope-filtered durable activity, at most 100 rows, opaque ten-minute cursor bound to principal/owner/domain/sequence |
| POST `/api/v1/session/refresh` and `/logout` | Verified browser renewal or local session revocation |
| GET `/live`, `/ready` | Private loopback profile only; minimal process liveness versus gateway/schema/epoch readiness |

Read routes take explicit owner_id/domain scope where applicable; that requested scope is checked against current membership, never used as an authority assertion. Existing role distinctions remain nonhierarchical. Request bodies use original bounded JSON through strict `from_wire`, with no framework coercion. Duplicate/unknown scope parameters, forged actor/role fields, altered/cross-actor cursors and stale writes fail. Successes use existing typed registry/configuration/storage DTOs; errors use ApiError with random request ID and fixed invalid/unauthenticated/forbidden/not-found/conflict/too-large/limited/unavailable codes (400/401/403/404/409/413/429/503). No SQL, exceptions, tokens or submitted payload appear in error bodies. `invalid` is the additive version-one error code; its generated schema is updated.

No chat, SSE, message-admission, approval, tool, membership-administration or provider-account route is exposed. Held proposals remain private M1-B storage, and M0's strict no-op event/parser/topology/executor remains unchanged. M1-D must add chat transport and atomic promotion as a separate reviewed change; M1-E supplies presentation and streaming. Text is returned as JSON with nosniff/CSP; actual browser rendering/XSS/accessibility acceptance remains M1-E.

## Bounds, schema and cleanup

The ASGI envelope requires HTTPS, exact configured Host and exact Origin when supplied; it trusts no forwarded headers and enables no CORS. It rejects duplicate sensitive headers/cookies, compressed input and unsupported content types/methods. It caps input at 64 KiB before parsing, headers at 64 entries/16 KiB, query at 8 KiB, output at 256 KiB and request work at ten seconds. Sixteen active requests per app are admitted without an in-process waiting queue; excess returns 503 before body buffering. An embedding server must additionally limit connections, header parsing and slow-client send time; the test server does so explicitly. Production listener enforcement remains M1-G. Responses carry no-store/no-cache, a restrictive CSP, nosniff, frame denial, no-referrer and request IDs. Access/payload logging is not enabled by this factory.

PostgreSQL enforces writes at 30/minute/principal, burst five, shared across factories/sessions; rejected conflicts still consume write budget. Logout remains available without consuming it. Four global five-second identity-operation leases are separate from execution/tool budgets; provider calls hold no database transaction or connection. Shared public-key refresh admits at most one attempt/60 seconds, caches at most 64 KiB/four validated keys for 60 seconds, and fails closed on cold/stale unavailable keys. The gateway retains a maximum four-connection pool, five-second statements and one-second locks. Container CPU/memory caps remain proposed, not applied.

Migration 0007 adds private RLS tables for login flows, encrypted provider credentials, shared public keys, identity leases and write buckets, plus narrow gateway functions. It creates no roles/account mappings. Migrations 0001–0006 are byte-preserved. API readiness compares the complete database migration version/checksum map to packaged reviewed fingerprints; missing, altered or future migrations block readiness and API access while `/live` stays available. Only explicit migration-role tooling applies SQL; startup never migrates.

Flows are capped at 100 globally, expire after five minutes and are single-use. Used browser verifier ciphertext is immediately cleared. Login admission removes expired/old-epoch flows and credentials expired/revoked for at least 24 hours or belonging to a prior epoch; logout removes its credentials immediately. This is opportunistic cleanup, **not a guaranteed daily purge**. Scheduled 24-hour secret cleanup, minimal audit/retention, current deletion/revocation reapplication and populated control-plane backup restore remain explicit M1-F/G acceptance gaps before real data. The fixture caps accounts, codes and active provider sessions at 100; fixture restart/new sign-in is required to recover lost issuer memory. No accepted chat work is evicted.

## Acceptance evidence and remaining gates

Local validation passes **73 optional-profile cases** and **535 base-runtime cases**; 15 broker/volume/lifecycle cases require hosted services and were explicitly skipped locally. The required hosted total is **623 cases**: 548 normal base cases, 73 optional API cases and two dedicated recovery/lifecycle drills. Lock, Ruff/format, strict mypy in both installations (73 files), 27 generated schemas, seven documentation-checker tests, 43-document inventory and whitespace checks pass. These counts are test evidence, not production readiness.

The required optional-profile suite includes real localhost HTTPS browser/CLI equivalence, callback/cookie behavior, stale revisions and current membership denial. Additional ASGI-plus-real-PostgreSQL cases cover signed-token forgeries/key rotation, PKCE/state/binding/code replay, ambiguous credentials, CSRF/Origin/Host/HTTP and byte rejection, membership/principal/provider/epoch invalidation, transactional login rollback, encrypted private credentials, shared rate/auth/flow limits, cursor scope, status freshness, authority outages and migration mismatch. Base configuration/schema/type/independence regressions remain separate from this optional installation. Hosted CI requires PostgreSQL and the optional profile; missing required dependencies/services cannot count as skipped success. Existing broker, volume-loss, lifecycle and standalone-wheel jobs remain required.

Covered subsets are M1-02/03/08/09/17/18 at the synthetic API boundary and API schema-readiness portions of M1-20. They do not complete the full M1 matrix: chat/dispatch, actual UI/CLI commands/SSE, governance/MCP, scheduled retention, combined API process/restore faults, production identity/network/keys, real data, VM availability/capacity and independent monitoring remain unpassed. See [[MarkOS v0.3 M1-B Control Plane Persistence]], [[MarkOS v0.3 M0-F Lifecycle and Closeout]] and [[Documentation Authority Index]]. No merge, deployment, real account, vault or personal note access is performed.
