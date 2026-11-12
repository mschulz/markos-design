---
title: ADR-005 Governed MCP Access for Agents
type: architecture-decision
status: proposed
version: 1.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
  - security
---

# ADR-005 Governed MCP Access for Agents

## Context and status

The owner requested MCP access for agents, documented in the specification and roadmap with permissions, domain isolation, approvals and audit. This ADR proposes the implementation boundary and acceptance gates. It enables no server, credential or tool and completes no runtime milestone. Review it independently of the open M0 runtime PRs.

MCP connects agents to approved tools and data sources. RabbitMQ remains the internal event bus; PostgreSQL remains authoritative for durable tasks, knowledge, approvals and audit. Immutable originals remain in object storage; Markdown is a derived export. Follow [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]]: no PARA, Obsidian, legacy application dependency or MacBook-dependent production service.

## Decision proposed

### 1. Access boundary and protocol profile

Add a logical MCP access gateway to the independent runtime's execution/tool boundary, governed by the shared control plane. It hosts MCP clients and mediates every agent request to a registered server. This is a logical boundary, not a commitment to a new VM, process per agent or public MCP server. Exposing MarkOS itself as an MCP server is outside this increment.

Agents submit typed call intents; they cannot open arbitrary connections, execute server launch commands or hold provider credentials. Broker subscriptions grant no tool access. Existing ChatGPT/Codex connector permissions grant no MarkOS runtime access. Discovery and capability negotiation never automatically enable tools or confer authorization.

The implementation PR must pin a supported SDK and explicit protocol-version allowlist, demonstrate interoperability, and reject unsupported versions/features without weakening policy. The official specification resolved to revision 2026-07-28 when reviewed on 12 November 2026; this is research evidence, not a dependency lock. Initially enable tool calls only. Resource reads need separate scoped grants before use. Prompts, sampling, elicitation, roots, apps, skills, long-running task extensions and other server-initiated/nested operations remain disabled until independently reviewed. MCP task handles never replace MarkOS durable tasks.

### 2. Permissions and credentials

Deny by default. Versioned agent grants bind trusted runtime principal, owner, domain, server identity/endpoint, approved tool name and input-schema digest, argument constraints, permitted source/resource identifiers, disclosure destinations, effect class and budget. Define timeouts, maximum request/result bytes, concurrency, request rates and monetary limits before enabling a tool. Reject missing or unclassifiable policy fields. Server descriptions and read-only/idempotent annotations are untrusted hints, not permission evidence.

The registry is administratively controlled and versioned. Discovery may suggest candidates, but new tools, endpoints, schemas, capabilities or permission expansion require review. A material change disables the affected grant and invalidates pending approvals. Reauthorize immediately before each dispatch, including retries, against current grant, policy, caller identity and source permissions. Revocation stops queued work and future dispatch; already-dispatched operations require reconciliation and cannot be promised to stop remotely.

For remote access require authenticated HTTPS endpoints with verified TLS and narrowly scoped, resource/audience-bound tokens. Implement the selected protocol's HTTP authorization flow where applicable; record any provider-specific authentication as a separately reviewed profile. Protocol-level authorization is optional in MCP; authenticated access is mandatory MarkOS policy. Keep encrypted secret references in configuration and resolve credentials only inside the controlled boundary. Never forward a caller's unrelated token, expose credentials to a model, or log secret values. Provider account consent is distinct from approval to perform a particular action.

Allowlist destinations and validate authentication/discovery/resource URLs, redirects, resolved addresses and egress. Prevent DNS rebinding and requests to cloud metadata endpoints; private network destinations require explicit reviewed internal-server rules. Do not forward credentials across redirect origins. Local stdio servers are deferred: later support requires pinned executables in a restricted cloud sandbox, explicit environment/egress/filesystem grants, no ambient credentials or personal/home/vault mounts, and its own review. No arbitrary model-supplied shell command is allowed.

### 3. Owner and domain isolation

Derive owner/domain/principal from authenticated runtime context, never model arguments. Check every input reference and returned resource against that scope. Partition grants, credentials, result storage, caches, any connection/session state and audit access by owner/domain/principal. Reuse connections only where isolation is proven; a server unable to constrain its underlying account/data scope is ineligible for that grant. Filtering model output after unrestricted retrieval is insufficient.

Cross-domain movement requires an explicit owner-approved transfer workflow identifying source, destination, data and purpose, plus audit at both boundaries; an MCP call cannot bypass it. Tool results are untrusted data: bound sizes, validate schemas/content, retain provenance and do not treat embedded instructions or suggested calls as authority. A subsequent tool call always passes the full policy gate. Returning a URL or resource handle does not authorize fetching it.

### 4. Approvals and execution outcomes

Classify both external effects and disclosure. A read-only tool can disclose private arguments to a third party. Bounded reads may run under a reviewed standing grant only for explicitly permitted sources, recipients and data classes; new disclosure or consequential mutation requires explicit scoped human approval through the shared authenticated approval inbox. Uncertain classification fails closed. Approval never overrides a denied grant or domain boundary.

Persist a preview and immutable call intent in PostgreSQL: owner/domain/principal, agent and task/run IDs, server identity, tool/schema version, canonical arguments digest, accessible bounded argument/data preview, source references/classification, recipient, effect, budget, policy/grant versions and expiry. Approval binds that exact intent. Changes to inputs, tool/schema, destination, permissions or policy require a new decision. Only an authorized human for that owner/domain may approve; an agent or server cannot approve its own request. Expiry, rejection, revocation and atomic one-time consumption are durable states, tested against concurrent claims and replay.

Before dispatch, atomically consume the approval where required and persist an authorized dispatch attempt with a stable call/operation ID. Recheck current authorization at dispatch. An approved call is not a blanket grant for later retries. An uncertain mutating attempt must not consume another approval and blindly repeat the effect. Persist unknown outcomes and reconcile with provider operation IDs or verified idempotency support; retry only when the connector can prove it is safe under the same intent and current permissions. Otherwise require human resolution. A safe bounded read may use its explicit retry policy. Timeout, cancellation, a lost response or database restore does not prove an external effect was rolled back. Event replay suppresses MCP external effects by default; no exactly-once guarantee is claimed.

### 5. Durable audit and recovery

Persist requested, denied, awaiting-approval, approved/rejected/expired/revoked, dispatched, succeeded/failed and unknown-outcome transitions in PostgreSQL. Commit the authorized intent/approval consumption/dispatch record before releasing credentials to a call. If policy, approval or audit storage is unavailable, do not dispatch. If recording a remote result fails, the prior dispatched record remains unresolved for reconciliation; never assume failure and silently repeat it. Leases and fencing prevent concurrent local claims but do not fence an external provider automatically.

Audit records include UTC times, trusted principal, owner/domain, agent version, task/run/event correlation and causation IDs, call/attempt/operation IDs, server identity, tool/schema digest, input/result hashes and scoped payload references, data classification/destination, policy/grant version, decision/reason, approval reference, retry count, provider operation ID, outcome and bounded redacted errors. Do not store raw credentials, full email bodies or unrestricted payloads in logs/events. Hashes do not replace controlled access to sensitive referenced data.

Use restricted append-only audit writer permissions and separate authorized audit readers; agents cannot rewrite their own history. Administrative repair/deletion must be separately authorized and audited. This is application-level audit integrity, not a claim of cryptographic tamper-proof storage. Backups, restore reconciliation and deletion/holds must include MCP records and payload references. Audit retention, access roles and erasure treatment require owner review before real data; ADR-003's event replay/dedup windows do not automatically set tool-audit retention.

## Delivery and acceptance gates

M0 remains the existing independent foundation; this ADR adds no M0 dependency or broker change. M1 introduces registry/policy/approval/audit contracts and a synthetic read-only MCP adapter. Real accounts and consequential tools remain disabled until their connector, isolation, consent and recovery tests pass in separate reviewed PRs. Later agents reuse this boundary; they never implement their own bypass clients. See [[MarkOS v0.3 Roadmap]].

These are future implementation tests, not executed evidence from this documentation PR:

| ID | Required acceptance evidence |
|---|---|
| MCP-01 | Synthetic allowed tool succeeds; unknown server/tool/schema, wildcard/unbounded grant, unsupported version/capability and discovery changes fail closed. No new tool auto-enables. |
| MCP-02 | Forged owner/domain/principal, unauthorized source/handle and cross-domain result/cache/credential/state access are denied. Explicit transfer has matching approval and audit. |
| MCP-03 | Read-only private-data disclosure to an unapproved recipient is blocked; standing grants cover only their exact classified scope. Effect/disclosure uncertainty denies dispatch. |
| MCP-04 | Approval preview matches immutable intent; changed args/schema/policy, wrong approver, expiry, revocation and replay deny dispatch. Concurrent claims consume approval once. |
| MCP-05 | OAuth/provider profile tests reject wrong-audience tokens and token passthrough; discovery/redirect/SSRF/rebinding cases cannot leak credentials or reach metadata endpoints. |
| MCP-06 | Malicious tool descriptions/results cannot enable tools, alter permissions or trigger nested calls; oversized payloads and exhausted timeout/rate/concurrency/cost budgets stop safely. |
| MCP-07 | Unavailable policy/approval/audit database means zero calls. Crash before dispatch and after remote effect/before result commit leaves recoverable evidence; unresolved effects are not blindly retried. |
| MCP-08 | Verified idempotency/reconciliation prevents duplicate effects across lost responses, lease expiry, cancellation, replay and restore; unsupported providers require human resolution. |
| MCP-09 | Audit captures allowed/denied/approval/unknown paths with correlation and redaction; agents cannot edit past records or read another scope. Revocation blocks queued/retried calls. |
| MCP-10 | Isolated SDK/server interoperability and cloud recovery demonstrate MCP works with MacBook off; RabbitMQ still carries internal events. No personal folders or legacy imports are required. |

## Alternatives and consequences

Direct per-agent clients are rejected because they duplicate policy, leak credentials and fragment audit. MCP as an event bus is rejected: tool calls cannot replace durable outbox/inbox and replay contracts. A single unrestricted server account with post-filtered outputs is rejected because it cannot establish source isolation.

The gateway concentrates policy and credentials, so its failure must fail closed and its service identity, egress and database privileges need independent security review. More bounded reads and explicit approvals reduce autonomy but make permissions reviewable. Native provider adapters remain valid when they provide better ingestion, permission or recovery semantics; Gmail watch/Pub/Sub is unchanged. No MCP server/provider is selected by this ADR.

## Unresolved implementation decisions

- Supported SDK/version, tested protocol versions, transport and first synthetic/real server; pin only after compatibility/security checks.
- Gateway package/process placement, trusted runtime authentication and scoped credential storage/rotation; no new infrastructure is provisioned here.
- First tool/source/destination allowlist, data/effect classification, standing-grant policy and measured byte/rate/concurrency/time/cost limits.
- Connector-specific idempotency/reconciliation evidence, audit retention/access/erasure/holds and operational owner before production use.

## Protocol sources

Reviewed 12 November 2026. These sources describe MCP; the stricter permissions, approval and audit rules above are MarkOS design proposals, not guarantees supplied by the protocol.

- [MCP specification, revision 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28): protocol roles and negotiated capabilities.
- [MCP HTTP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization): transport authorization, distinct from MarkOS action approval.
- [MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices): token, proxy, discovery, egress and local-server risks.

Related: [[MarkOS v0.3 Specification]], [[MarkOS v0.3 Roadmap]], [[ADR-0002 GitHub as Canonical Project Record]], [[ADR-003 RabbitMQ Internal Event Bus]].
