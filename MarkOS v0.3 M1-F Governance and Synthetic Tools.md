---
title: MarkOS v0.3 M1-F Governance and Synthetic Tools
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
  - governance
---

# MarkOS v0.3 M1-F Governance and Synthetic Tools

## Merged-main review and scope

Reviewed main `231a018233439d22dca22ea9f6bfdb67be7d6e83` after PR #23 merged. Its tree is exactly reviewed M1-E head `675ca2e90eb81685ae83d78a934e7c53dbee7b2e` (tree `34b2ebbd49a4b9d2feaa400514b3dc836369a2c0`). All six post-merge checks pass: [runtime/API/browser/recovery CI](https://github.com/mschulz/markos-design/actions/runs/43333911281) covers 770 cases and [documentation CI](https://github.com/mschulz/markos-design/actions/runs/43333911388) passes. Review of client session storage, profile selection, current permissions/CSRF, cursor binding, bounded streaming and migration preservation found no blocking issue within the synthetic M1-E scope. Its production, process/restore and tool-governance limitations remain explicit.

The owner separately authorized M1-F in [[MarkOS v0.3 M1 Control Plane Implementation Plan]]. This PR adds a fixed synthetic governance path inside the independent runtime. It follows [[ADR-005 Governed MCP Access for Agents]] and [[ADR-006 Control Plane Identity and Trust Boundary]]. GitHub remains canonical for project artifacts; PostgreSQL owns durable operational/approval/audit state, RabbitMQ remains the internal bus, originals remain source objects and Markdown remains derived. There is no PARA, Obsidian or archived application dependency. MCP does not replace RabbitMQ or change the rules-first M3/native-adapter decisions. Nothing is merged or deployed by this slice.

## Closed profile and reviewed permissions

`SyntheticGovernanceApiConfig` extends the explicit web/conversation fixture profile and requires `governance_profile=synthetic-governance-v1`. Existing M1-A preflight and M1-C/D/E profiles retain their restrictions. General `tool_execution_enabled` and `real_accounts_enabled` remain fixed false; the additional profile permits only this reviewed synthetic fixture path. It is constructed explicitly, never selected through an HTTP config/registry field. There is no API/tool-worker launcher or production connector.

The separately locked optional `tools` extra pins official `mcp==2.3.0` and protocol **2026-07-28**. Existing base/control/browser versions remain unchanged. Base installation and the CLI do not require MCP. The adapter uses SDK request/result models through a controlled HTTP path, with actual official SDK server interoperability tests for JSON and request-scoped SSE. It does not use high-level auto-negotiation, HeaderMismatch retry, MRTR input drivers, interactive OAuth or implicit SDK credential discovery. Each wire request declares modern protocol/client metadata and matching method/name headers. No legacy initialize/session/GET-stream fallback is allowed.

Only `fixture.read` with the package's exact bounded label schema is eligible. The server identity/version, capability set, schema and configured endpoint are checked before dispatch. New discovery never enables a permission. Server descriptions/annotations are untrusted data and confer no authority. Prompts, resources, sampling, elicitation, roots, apps, tasks, subscriptions, arbitrary source handles/URLs, stdio, real models/providers and paid or consequential tools remain unavailable. A tool response cannot request another operation.

An administrator may review a contiguous `ReviewedFixtureGrant` revision for the current owner/domain, enable/disable that fixed fixture grant and choose exact human approval or a standing synthetic-read grant. Registration alone enables nothing. The grant binds owner/domain, fixed agent/server/tool/schema/protocol, exact endpoint, synthetic data class, recipient and read effect. Revisions are immutable history; changes invalidate unconsumed approvals. The original inert `ToolGrant` descriptor still requires enabled=false. The new reviewed model is a separate profile contract, not a relaxation of preflight.

The only eligible endpoint is canonical numeric loopback HTTPS `https://127.0.0.1:PORT/mcp`, port 1024–65535. No DNS, redirect, discovery URL, proxy, metadata address, external origin or unapproved path is followed. TLS uses an explicitly supplied CA. The dispatcher receives a separate opaque fixture credential through explicit in-memory configuration; it never forwards a human API/provider bearer or selects a secret from model arguments. The fixture validates credential plus exact resource/owner/domain headers before its handler can access data. This is a deliberately separate synthetic authentication profile, **not an OAuth/provider integration**. Real audience/resource/account isolation, consent and credential custody remain connector-specific gates.

## Durable lifecycle and fresh execution authority

| Phase | Durable behavior |
|---|---|
| Admission | Operator submits typed `GovernedChatRequest` to the shared API with synthetic arguments and a stable operation key. One transaction commits the existing human message/request event/exact consumer/outbox plus the governed plan and audit. Same key/body returns the receipt, including at capacity; changed arguments or changing ordinary/governed operation profile conflicts. |
| Initial execution | The existing RabbitMQ route hands off to the scoped durable task. Only `claim_governed_chat` selects governed plans; the ordinary no-tool worker cannot consume them. Current human/session, agent/configuration, epoch, task fence/lease and shared execution slot are checked. |
| Suspension | The fixed worker creates one immutable `CallIntent`, a bounded preview/digests and an awaiting or standing-grant-approved record. It releases the execution slot, lease and transaction and returns. No broker delivery, live waiter or connection spans human approval. |
| Human decision | Only a current scoped approver may approve/reject/revoke the exact intent hash and expected revision. Administrator/operator roles do not imply approver. The decision is audited and never dispatches from the HTTP handler. Automatic expiry/revocation is distinct from a human decision. |
| Resume and admission | A new worker object obtains fresh current authority and an execution lease. Known tool/rate/outcome-capacity exhaustion prevents claiming work. A capacity race durably defers the undispatched task and immediately releases its slot; it does not consume the approval or count as a tool attempt. |
| Dispatch | After bounded identity/metadata inspection and another current upstream/local check, one short transaction rechecks task/executor/lease/epoch, requester, grant/policy/schema/destination, approver authority, expiry and budgets; it reserves capacity, consumes authority once and commits dispatch/operation ID plus audit before the call. |
| Result | Network work holds no database transaction/connection or broker delivery. A valid exact-scope synthetic result is stored with checksum and audit; the existing deterministic reply/result/activity/completion outbox commits through the established fenced conversation path. |
| Uncertainty | Lost response, invalid result, cancellation, stale lease/restore or failed result recording preserves the prior dispatch as unknown/unresolved. Its outcome capacity stays reserved. It is never blindly called again. |

The immutable intent binds scope, requester, agent/configuration/grant versions, task/run/epoch, operation, suspension generation, exact versioned arguments/preview hashes, recipient/effect/classification, expiry and one attempt. The SQL/Python canonical forms are checked against each other; they are the existing sorted compact UTF-8 v1 binding, not a general RFC 8785 claim. The fixed wire mapping sends the label from the versioned control argument object to the reviewed MCP schema.

**Fresh-lease refinement:** `task_fence` in the approved document binds its suspension generation. A private `resume_fence` links that unchanged intent to each freshly claimed execution lease. Dispatch checks that mapping plus current task/executor/fence/epoch and permissions. It never treats the old suspended lease as current authority. Budget deferral or recovery changes neither approved arguments nor intent hash, and stale executors cannot dispatch or finalize. Once a dispatch exists, the unique intent/dispatch binding forbids another attempt. The initial profile is stricter than ADR-005's general proposed retry/run ceilings: **one fixture call and one attempt per conversation run, no automatic tool retry**. Recovery of a stored successful result can finish the reply without calling the tool again.

Requester session loss/revocation, disabled principals, membership removal, agent/configuration or grant changes, rejection, expiry and cancellation block subsequent dispatch. A late remote operation cannot be rolled back by a local fence. Unknown work remains distinguishable from failure. A scoped approver may record an explicit synthetic-ledger finding with expected dispatch revision. This is human-attested reconciliation, not verified provider reconciliation: resolving it releases reserved capacity and ends the affected chat run without manufacturing a reply or retry. The test-only fixture ledger demonstrates an operation committed once before a lost response; its operation ID matches the durable dispatch record. No real mutation adapter exists.

## Shared API, web and CLI

All routes require the existing current authentication, scope checks, bounded original-JSON parsing, write admission and cookie Origin/CSRF protections. Missing/inaccessible IDs do not reveal another scope. Existing non-governance profiles retain the disabled approval placeholder and no governance routes.

| Route | Behavior |
|---|---|
| GET/POST `/api/v1/tools/grant` | Scoped reviewed grant read or administrator revision |
| POST `/api/v1/tool-requests` | Typed durable operator admission; no selected endpoint/tool/actor/secret in request |
| GET `/api/v1/approvals` and `/{id}` | Typed exact scoped previews/status, with bounded purpose/actor/scope/epoch cursors |
| POST `/api/v1/approvals/{id}/decisions` | Approver's expected-revision exact intent decision |
| GET `/api/v1/tool-dispatches` and `/{id}` | Scoped durable outcome/operation/result references |
| POST `/api/v1/tool-dispatches/{id}/reconcile` | Exact reviewed human ledger finding; no remote call |
| GET `/api/v1/tools/audit` | Bounded scoped append-only metadata history |

`GovernedApproval`, `GovernedDispatch` and `GovernanceAudit` are strict public view contracts. The new approval view explicitly distinguishes standing grants, human decisions and automatic revocation; the old inert `ApprovalRecord` remains unchanged. Sensitive previews/results can be absent after cleanup while minimal status/checksums remain accessible. The existing chat-status contract adds awaiting-decision and unknown states; ordinary conversation behavior is preserved. Reference-only tool activity is committed alongside audit and uses the existing durable activity stream, without tool payloads or a new broker route.

Configuration shows the synthetic grant and administrator's exact review; Conversations can request a reviewed fixture read; Approvals shows immutable arguments, destination, effect/classification, versions, expiry, digest and decision preview. Canceling a preview writes nothing. Approver decisions and human reconciliation are separate actions. An unknown outcome is never labelled rolled back. Plain-text DOM insertion, CSP, no private browser storage, keyboard controls, visible workspace and narrow-screen wrapping remain in force. Tests use independent requester/approver browser sessions: logging the requester out correctly revokes its pending authority.

The base-install CLI adds `tool-grant`, `tool-grant-preview`, `tool-grant-apply`, `tool-send`, `approval-decide`, `tool-dispatches`, `tool-audit` and `tool-reconcile`. They use the same HTTPS API and existing explicit CA/origin/private session file. Inputs come from typed JSON files/stdin. Grant/decision/reconciliation commands preview the exact request and current record; applying requires its digest. A digest is neither authentication nor permission. Client output includes no bearer or fixture credential; clients have no direct SQL/broker/tool-network path.

## Migration, admission and maintenance

Additive `0010_synthetic_governance.sql` adds private RLS grant history, governed plans/intents, dispatch reservations, tool slots/rates, append-only audit, explicit holds and minimal anti-replay tombstones. Narrow definer functions enforce current delegated human or scoped runtime identity. It adds the approver capability without changing the other nonhierarchical roles. Runtime/gateway roles cannot mutate base tables, self-grant, rewrite audit, invoke private old claim/finalization bypasses, rotate restored generations or run cleanup. It provisions no cluster role, account, broker binding or production configuration. Migrations 0001–0009 and archived import/source/package/lock remain byte-preserved. Startup readiness fingerprints require 0010; startup never migrates.

| Bound | Enforced synthetic profile |
|---|---|
| Execution/tool admission | Existing two global execution slots; separate two global tool slots; one call/attempt per run |
| Queued/approval/outcome storage | 100 queued plans globally; 100 awaiting/approved intents globally and ten per owner/domain; 100 reserved/unresolved dispatches globally and twenty per scope. Existing chat limits can impose a tighter bound. |
| Rate | Database-shared 10 dispatched attempts/minute per scope, token-bucket burst two; retries cannot bypass counters |
| Payload/network | Existing 64-KiB HTTP envelope and 256-character synthetic label; at most 64-KiB serialized MCP request and 1-MiB cumulative response per wire exchange, including streamed bytes/comments. Unsupported compression is refused before decompression. |
| Deadlines | Explicit CA, connection at most five seconds, read idle at most ten seconds and monotonic absolute deadline. The parent synthetic agent's ten-second total work ceiling further limits metadata/authentication/call/result work; progress cannot reset it. |
| Identity/metadata work | Existing shared four identity-operation leases; at most two metadata wire requests per inspection, five-second inspection ceiling, bounded parent claim and no interactive OAuth or hidden SDK retry |
| SQL/client bounds | Existing pool four, statement five seconds, lock one second; API page at most 100 and response 256 KiB. Browser/CLI previews remain read-only until an explicit version-checked apply. |

Admission is atomic across connections/worker objects. Excess is refused/deferred with durable evidence; accepted work is not evicted. Tool/outcome slots are not extra agent execution slots. OS/container enforcement of the proposed tool 512-MiB/0.5-CPU ceiling and shared-VM/proxy throughput measurements remain M1-G/production work.

Minimal audit stores current trusted principal/scope, event/task/run/epoch/fence, intent/dispatch/operation IDs, hashes, schema/grant/policy versions, classification/recipient/effect, outcome/reason and UTC timestamps. Payloads/credentials are excluded from audit and broker activity. Optional exact argument/preview/result copies remain in private scoped rows only. This is application-level append-only permission enforcement, not cryptographic tamper-proof storage.

`cleanup_tools()` is an **explicit migration-role-only, bounded** maintenance operation: up to 100 payload or terminal intent groups and 1,000 eligible standalone audit rows per call. Seven days after terminal transition, it removes optional intent/plan/result copies; at 365 days it can remove terminal tool metadata/audit/dispatch records after committing minimal hashed operation tombstones. Those tombstones block re-admission of retired operations without retaining the payload. Unresolved/reserved outcomes, active work and explicit holds protect required evidence. Holds remain protective after review date until privileged review removes them; due holds and unresolved outcomes older than 24 hours are reported. Current active grant history remains needed policy evidence. Cleanup records its own scoped metadata transitions and never deletes canonical source originals or conversations. No new diagnostic-log store is introduced; fourteen-day diagnostic retention, daily scheduling, growth monitoring, privacy/erasure and independent backup expiry remain M1-G/real-data gates. The private M0 test policy clock is reused only by isolated migration-role tests; clients/runtime cannot replace it.

Restore rotation invalidates pending/unconsumed authority and old leases. Dispatched work remains unknown with its reservation, operation ID and audit. No restored queued tool work auto-dispatches. Full populated API/tool process backup/restore, independently current deletion/revocation reapplication and external-provider reconciliation remain M1-G/connector work.

## Acceptance evidence and outstanding gates

Local checks pass 641 base cases, 207 API cases, all eight browser cases and the final targeted governance/fingerprint matrix. Twenty-one broker/dedicated recovery cases remain explicitly unpassed locally and are required in hosted CI. Lock, Ruff/format, strict typing in base and control/tools environments (107 files), 40 schemas, independent base wheel/CLI/assets and seven documentation tests pass. The 46-document inventory and whitespace checks pass. The required hosted matrix is 882 runtime tests: 654 normal base, 218 API/transport/tool, eight browser and two dedicated recovery drills. The PR records final hosted results and check links. Required CI retains independent base/optional control, PostgreSQL/RabbitMQ, standalone installation, lifecycle, volume-loss and documentation gates. The API job additionally installs the locked tools extra and requires MCP tests; browser acceptance includes the real governance flow. The full broker case exercises the existing route through durable approval to SDK fixture and completion; missing local RabbitMQ is an explicit skip, not passing evidence. Capacity fixtures seed only owned synthetic databases through the migration identity and are counter/atomicity tests, not cloud throughput measurements. There are 40 generated schemas and sanitized disabled grant/profile/request/result/reconciliation examples.

| Gate | M1-F evidence and remaining disposition |
|---|---|
| M1-15 / MCP-04 | Exact preview/hash/revision, role separation, changed destination/schema/grant/policy, expiry/revocation and competing one-time dispatch; decisions cannot dispatch directly |
| M1-16 | Durable slot-free waiting, reconstructed worker/client objects, fresh execution mapping, cancellation/session loss and no automatic uncertain replay; packaged OS-process kills remain M1-G |
| M1-18 / MCP-06 | Shared ready/approval/reservation exact/one-over bounds, capacity deferral, rate/fence recovery, fixed single attempt, scoped SDK byte/time limits and inherited execution/HTTP limits; VM CPU/memory/proxy/capacity measurement remains open |
| M1-19 / MCP-07/08 | Audit failure before dispatch gives zero fixture calls; response/result-recording faults preserve one remote ledger operation and unknown state; append-only roles and explicit human reconciliation |
| M1-20 | Preserved migration bytes, additive populated upgrade/regression and epoch invalidation of pending/active authority; full fresh backup/process restore/current privacy revocations remain M1-G |
| M1-21 / MCP-01/02/03/05/09 | Fixed SDK/protocol/server/schema, synthetic standing/approval grants, before-data owner/domain/resource credential binding, cross-scope result rejection, redirect/metadata/DNS denial, bounded/compressed/unsupported nested input rejection and inert text; real source/transfer, OAuth/audience/account/provider and deletion/backup evidence remain unpassed |
| M1-22 / MCP-09 | Controlled seven/365-day boundaries, pending/unresolved/hold protection, tombstone replay suppression and 24-hour/due-hold reporting; daily service/alert/log/erasure/backup operations remain open |
| MCP-10 | Actual official SDK local HTTPS JSON/request-SSE interoperability; no real connector, cloud recovery or MacBook-off operational evidence |

This is a reviewable synthetic governance slice, not whole-M1 or production closeout. M1-G owns packaged API/web/tool/conversation processes, required process/restore/browser fault evidence, operational maintenance and runbook/gap-ledger closeout. Production identity/MFA/recovery, OAuth/account consent, real credential custody, paid/effect tools, source/resource/transfer access, privacy retention/erasure, VM capacity, monitoring and cloud deployment remain separately reviewed decisions. Native Gmail/Pub/Sub and M3 routing do not acquire an MCP prerequisite. No personal data, merge or deployment is involved.

Sources checked 13 November 2026: [official MCP Python package](https://pypi.org/project/mcp/), [official 2026-07-28 release](https://github.com/modelcontextprotocol/modelcontextprotocol/releases), [modern Streamable HTTP specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) and the installed pinned SDK's request/result/server implementation. These support protocol/dependency behavior; MarkOS grants, fencing, audit and bounds are additional application controls.

Related: [[MarkOS v0.3 M1-E Web and CLI]], [[MarkOS v0.3 M1-D Durable Test Agent Conversation]], [[MarkOS v0.3 M0 Runbook]] and [[Documentation Authority Index]].
