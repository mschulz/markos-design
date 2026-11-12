---
title: MarkOS v0.3 M1-A Contracts and Identity
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

# MarkOS v0.3 M1-A Contracts and Identity

## Scope and PR #18 review

The owner authorized M1-A after requesting consistency/CI review of PR #18. Reviewed planning head `082f5d7a39ad2d7212f1b797862b7773f5f381d9`: documentation CI passes; no blocking inconsistency with the independent PostgreSQL/RabbitMQ architecture, governed MCP or rules-first M3 decisions. The plan distinguishes synthetic acceptance from production readiness and explicitly identifies human identity and typed chat as new work. Its seven-slice/22-case matrix remains future work; only M1-A contract portions are implemented here.

PR #18 remains unmerged. This branch preserves its commit and targets main; the cumulative PR includes the plan. Compare from `082f5d7` for M1-A-only work. No main merge, deployment, real account, provider call, personal folder access or legacy code reuse is performed. Current M0 runtime behavior, migrations 0001–0005, broker profile/topology, archived package/lock/source/tests, exact snapshots and import manifest remain intact.

## Implemented artifacts

`contracts/control.py` supplies strict version-one descriptors for human principal/membership, disabled agent revision and requested state change, configuration revision, conversation/message, chat input/event references, activity, disabled fixture tool grant, immutable synthetic intent/decision/approval and bounded audit/error records. These are data, not authentication or durable approval authority. Role support is a pure explicit matrix, not a request authorization function. All models reject unknown fields; UUIDs are nonnil, timestamps are UTC, revisions are typed, chat text is bounded by UTF-8 bytes and request parsing rejects duplicate/nonstandard/oversize JSON. Human request inputs cannot supply actor/owner/role/secret/tool fields.

Chat wire contracts are separate from the active M0 event union. Planned producer/consumer/routes and nested owner/domain reference matching are checked, but existing M0 transport/store/worker still reject chat types. No subscription or grant is provisioned. Intent hashing revalidates even unsafe copied models, binds exact versions/arguments/preview/recipient/run authority and permits only an expiring synthetic read. Atomic one-time consumption, reauthorization and audit immutability require M1-B/F database implementation; a valid hash is not permission.

`control/config.py` defines explicit inert test configuration and frozen `ControlLimits`. Gateway, chat execution, tools and real accounts are literal false; a caller cannot enable them. Secret reference names are validated but never resolved. The synthetic issuer origin, ES256/audience descriptor, cookie profile, key cache bounds and limits are frozen. Adjacent key filename is syntax only; the deliberately empty JWKS placeholder is neither opened nor trusted. See [[ADR-006 Control Plane Identity and Trust Boundary]] for decisions, role matrix, delegation compromise implications, session/provider revocation, retention and production gates.

`preflight-control --config <explicit JSON> [--event <explicit chat JSON>]` validates only those supplied documents. It does not authenticate, discover configuration, resolve secrets, open a database/socket, bootstrap keys or run tasks. There is no `serve-control` command. The original no-op preflight and confirmed synthetic lifecycle remain distinct.

Eighteen new generated schemas/examples extend the prior eight artifacts to **26 schemas**. Examples are synthetic and public; they are not active registry state. JSON Schema catches structural/type constraints; UTF-8 byte bounds, cross-field scope/time/digest checks and actual authority require Python/database checks. Do not treat a schema validator alone as authorization or full semantic validation.

The independent lock adds an optional `control` extra for the proposed API stack. The base runtime does not install/import it. A separate CI compatibility job uses only in-memory ASGI, ephemeral ES256 keys, escaped templates and server-config construction; it starts no listener or MarkOS login service. Existing required runtime/lifecycle/broker jobs remain intact. The clean-wheel job additionally runs control preflight outside the checkout.

## Acceptance evidence and limits

| M1 requirement | Evidence in M1-A | Still required |
|---|---|---|
| M1-01 | Guarded subprocess preflight outside checkout denies network/adapter imports and unintended files, ignores legacy settings, resolves no secrets; standalone base wheel exercises both preflights | Actual API imports/web image/browser/CLI boundary in later slices |
| M1-02 | Synthetic issuer/token-kind descriptor, strict types/UTC/version/key-config shape; optional ES256 primitive and wrong-audience compatibility probe | MarkOS signature/key rotation/expiry/provider session verifier and authentication middleware do not exist |
| M1-03 | Forged human input authority fields rejected; scoped reference/config matching, nonhierarchical role matrix and invalid membership descriptors | Current DB membership/session lookup, RLS/functions and cross-request/pool/stream isolation |
| M1-18 | Every frozen limit rejects one-over/boolean/float changes; exact document/UTF-8 byte boundaries and disabled feature switches | Distributed admission, actual resource/rate/session/stream enforcement, metrics and capacity |
| Other cases | Intent/approval structural binding and stable digest mutation tests inform future tests | No claim of passing M1-04–M1-22 or MCP-01–MCP-10 end-to-end |

The new suite adds **148 contract/preflight cases** to M0's 323-case acceptance set, for **471 runtime cases** including its two dedicated harness drills. The optional compatibility probe is additional evidence, not counted as a pytest case. Required hosted CI must run the unchanged PostgreSQL/RabbitMQ/lifecycle/volume-loss cases as well as new cases; absent local broker tests skip explicitly. This slice does not replace those checks with fakes or claim a completed M1 authentication system.

## Migration and next slice

No SQL migration is added; M1-B begins with additive 0006 after reviewing these contracts and trust decisions. It must implement narrow non-admin gateway functions, current memberships/sessions/revisions, scoped conversations/activity/idempotency and upgrade/role tests before M1-C can bind an authenticated actor to a real request. M1-D extends typed chat transport/SQL/execution together. Existing no-op wire behavior and epoch fencing remain regression gates.

Real Supabase Auth onboarding, refresh/credential custody, provider-specific revocation, current deletion records, MCP client/server/SDK/protocol compatibility, paid/private data, production retention and host/backup/monitoring acceptance remain unresolved launch gates. Production readiness stays false. Native Gmail/Pub/Sub and interchangeable M3 classifiers remain independent of MCP acceptance.
