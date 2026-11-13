---
title: ADR-005 Governed MCP Access for Agents
type: architecture-decision
status: proposed
version: 1.3
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-13
reviewed: 2026-11-13
tags:
  - architecture
  - security
---

# ADR-005 Governed MCP Access for Agents

## Context and status

The owner requested MCP access for agents, documented in the specification and roadmap with permissions, domain isolation, approvals and audit. This ADR proposes the implementation boundary and acceptance gates. The original ADR enabled no server or credential. [[MarkOS v0.3 M1-F Governance and Synthetic Tools]] now implements a separately reviewable synthetic subset; real servers/accounts/effects and operational closeout remain gated. Review it independently of the merged M0-A–M0-C foundation and subsequent M0 implementation slices.

MCP connects agents to approved tools and data sources. RabbitMQ remains the internal event bus; PostgreSQL remains authoritative for durable tasks, knowledge, approvals and audit. Immutable originals remain in object storage; Markdown is a derived export. Follow [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]]: no PARA, Obsidian, legacy application dependency or MacBook-dependent production service.

## Decision proposed

### 1. Access boundary and protocol profile

Add a logical MCP access gateway to the independent runtime's execution/tool boundary, governed by the shared control plane. It hosts MCP clients and mediates every agent request to a registered server. This is a logical boundary, not a commitment to a new VM, process per agent or public MCP server. Exposing MarkOS itself as an MCP server is outside this increment.

Agents submit typed call intents; they cannot open arbitrary connections, execute server launch commands or hold provider credentials. Broker subscriptions grant no tool access. Existing ChatGPT/Codex connector permissions grant no MarkOS runtime access. Discovery and capability negotiation never automatically enable tools or confer authorization.

The implementation PR must pin a supported SDK and explicit protocol-version allowlist, demonstrate interoperability, and reject unsupported versions/features without weakening policy. The official specification resolved to revision 2026-07-28 when reviewed on 12 November 2026; this is research evidence, not a dependency lock. Initially enable tool calls only. Resource reads need separate scoped grants before use. Prompts, sampling, elicitation, roots, apps, skills, long-running task extensions and other server-initiated/nested operations remain disabled until independently reviewed. MCP task handles never replace MarkOS durable tasks.

### Proposed SDK and execution profile

All choices in this profile remain **proposed**, unmeasured starting values. The original proposal installed no dependency. M1-F now locks the optional SDK/protocol and implements a narrower fixed synthetic profile; deployment resource ceilings and real-provider choices remain proposed and unmeasured. Revalidate release/security status, resolve the independent runtime lock, prove compatibility and measure shared-VM contention in the implementation PR before enablement.

| Area | Proposed choice and semantics |
|---|---|
| Python SDK | Official **`mcp==2.3.0`**, without CLI extras; lock transitive dependencies in `services/runtime/`, independently of the archived application |
| Transport and protocol | Authenticated HTTPS Streamable HTTP; initially allow **2026-07-28** only. Validate server identity/capabilities and compatibility during registration and reconnect; no silent fallback. Stdio and legacy SSE remain deferred |
| Gateway resource ceiling | **512 MiB RAM, 0.5 CPU** for the logical gateway's aggregate deployment, within the shared 4-vCPU/8-GiB VM envelope. If embedded in another service, account for this budget in that service rather than claim a separate container limit. No local model inference |
| Concurrent tool attempts | **2 across the VM**, **1 per agent run**; enforce shared admission rather than multiply limits per worker/replica |
| Payload bounds | **64 KiB serialized arguments; 1 MiB cumulative decoded response per attempt**, including streamed events. Enforce before unbounded buffering/parsing for JSON and SSE, including decompression; an SSE event cap alone is insufficient. Broker messages retain references, not tool payloads |
| Deadlines | **5-second connection**, **10-second read-idle**, **30-second absolute attempt**, **60-second total execution/retry budget** from first dispatch, including backoff and authentication work; progress must not reset the absolute deadline |
| Rate/run admission | **10 dispatched attempts/minute per owner/domain**, token-bucket burst **2**; **10 tool attempts per agent run** including retries. Validate budgets before dispatch; discovery/authentication also consume bounded control-request and byte/time budgets within the gateway ceiling |
| Durable ready backlog | At most **100 ready calls globally**, atomically enforced across replicas. Backpressure/refuse new admissions with an explicit recorded outcome; never discard already-accepted work. Approval wait and unresolved-outcome records are distinct from ready calls and require monitored storage/admission bounds before real use |
| Retry and cost | At most **one retry for a proven-safe read**, within all attempt/rate/time budgets and after reauthorization. No blind retry of uncertain mutations. Synthetic phase permits **no paid tools**; paid connectors require separately approved monetary ceilings |

SDK behavior is not an authorization boundary. Version 2.3.0 can re-list tools and retry after a `HeaderMismatch`; prevent that automatic path from bypassing grant/schema review, approval, audit or attempt limits. Use a controlled lower-level path if the high-level client cannot gate each wire attempt. A schema change fails closed for review, rather than accepting a newly discovered schema and retrying. The SDK can exclude interactive OAuth waits from request timeouts: the gateway's outer deadline still applies; account onboarding is a separate bounded user workflow, never an unattended tool worker waiting indefinitely. Version pinning alone does not verify a server's identity or capabilities.

### M0 execution and M3 classification integration

Merged M0-A–M0-C implements synthetic no-op contracts, PostgreSQL storage and RabbitMQ handoff; it does not implement an MCP client, general tool executor, approval consumption or gateway admission. Add those capabilities only through reviewed contracts/migrations and isolated acceptance. Preserve the existing strict event/identity checks, intended-consumer state, transaction boundaries, transport limits and regression evidence; MCP calls do not belong in broker callbacks or scheduler scans.

Dispatch tools from durable task execution with current database-clock task/run authority. Provider network work occurs after short claim/authorization/audit transactions commit, with no database transaction or broker delivery held across the call. Claim gateway capacity and persist dispatch authorization atomically, rechecking task fence/lease, grant, approval, source access and remaining run budget at that boundary. A stale worker cannot authorize a new call or finalize a result. A lease can expire after a request leaves the gateway; no local fence guarantees cancellation or rollback at the provider. Preserve the prior dispatch record and reconcile unknown outcomes before retrying an effect.

The proposed two global gateway attempts are a separate nested limit inside authorised agent runs, not two extra agent executions. Do not multiply either budget per replica or create an unbudgeted detached task when a call waits for capacity/approval. Approval waiting must use durable resumable state, without a live broker delivery, transaction, execution slot or in-memory waiter held indefinitely. The implementation must define release/reclaim/renewal and immutable resume identity together so an expired run or changed approval cannot dispatch. Bound the call by the lesser of its gateway deadline and the authorised run's remaining time; task cancellation and gateway cancellation still require external-outcome reconciliation.

Rules-first M3 classification retains its interchangeable Jev/LLM adapters. An MCP-enabled classifier/tool path uses this gateway's grants and disclosure checks; a native provider adapter remains independent of MCP acceptance and still needs its own reviewed privacy, cost and task-execution controls. Model output cannot expand tool grants, change domain, or bypass the category-to-agent policy.

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

### Proposed audit retention profile

These are engineering proposals for owner review, not a legal retention requirement or an applied deletion policy. Tool audit is separate from ADR-003's 30-day event replay and 90-day deduplication floor.

| Record | Proposed retention |
|---|---|
| Minimal audit/approval metadata, operation IDs and terminal outcomes | **365 days after the latest terminal transition or attempt**, whichever is later; never purge records still needed for unresolved work, deduplication or an explicit hold |
| Optional argument/result copies and sensitive approval previews | **7 days after completion or approval expiry**. Avoid copies where possible; retain only minimal necessary reconciliation evidence for unresolved work under an explicit reviewed hold |
| Redacted diagnostic logs | **14 days**; no credentials or unrestricted source payloads |

Alert on unresolved outcomes after **24 hours**; retain minimal reconciliation/idempotency evidence until resolved. Holds identify owner, purpose and review/expiry date and require periodic review; they do not justify indefinite retention of full payloads. Expired/rejected/revoked approvals cannot authorize a later call even while their audit metadata remains. Still-active previews require a bounded approval expiry policy before real use.

Run dependency-aware daily cleanup with scoped deletion permissions and an audit record. Purging audit copies must neither delete canonical source evidence nor silently extend source retention. Authorized privacy deletion can supersede routine retention through the explicit deletion workflow; preserve only permitted tombstones that block replay. Keep the cloud proposal of **7 daily and 4 weekly encrypted independent backup copies**; expiry is distinct from live-data deletion. Document managed backup retention before real data. Restores must reapply deletion records and reconcile external outcomes before releasing work, so deleted data is not resurrected into service. Payload encryption, erasure/hold handling, cleanup boundary tests and storage-growth monitoring remain implementation gates.

## Delivery and acceptance gates

M0 remains the existing independent foundation; this ADR adds no M0 dependency or broker change. M1 introduces registry/policy/approval/audit contracts and a synthetic read-only MCP adapter. Real MCP accounts and consequential tools remain disabled until MCP-01–MCP-10, including connector-specific interoperability and recovery evidence, pass in separate reviewed PRs before the first real MCP connector. These gates do not block native Gmail/Pub/Sub or other non-MCP adapters. M7 repeats and hardens MCP recovery/operational drills; it is not permission to defer baseline safety until after enablement. Later agents reuse this boundary; they never implement their own bypass clients. See [[MarkOS v0.3 Roadmap]].

These are future implementation tests, not executed evidence from this documentation PR:

| ID | Required acceptance evidence |
|---|---|
| MCP-01 | Synthetic allowed tool succeeds; unknown server/tool/schema, wildcard/unbounded grant, unsupported version/capability and discovery changes fail closed. No new tool auto-enables. |
| MCP-02 | Forged owner/domain/principal, unauthorized source/handle and cross-domain result/cache/credential/state access are denied. Explicit transfer has matching approval and audit. |
| MCP-03 | Read-only private-data disclosure to an unapproved recipient is blocked; standing grants cover only their exact classified scope. Effect/disclosure uncertainty denies dispatch. |
| MCP-04 | Approval preview matches immutable intent; changed args/schema/policy, wrong approver, expiry, revocation and replay deny dispatch. Concurrent claims consume approval once. |
| MCP-05 | OAuth/provider profile tests reject wrong-audience tokens and token passthrough; discovery/redirect/SSRF/rebinding cases cannot leak credentials or reach metadata endpoints. |
| MCP-06 | Malicious tool descriptions/results cannot enable tools, alter permissions or trigger nested calls. Prove separate global execution/gateway limits across replicas, no detached unbudgeted work and the lesser-of-run/gateway deadline. Test exact byte/time/rate/run/backlog/concurrency limits and one-over boundaries, streamed/JSON/compressed oversize responses, slow progress, OAuth waits, SDK hidden retries and multiple replicas; exhaustion stops safely. No paid synthetic tools. |
| MCP-07 | Expired task fences, revoked authority or exhausted run budget prevent new dispatch and stale finalization. Approval waits hold no live broker delivery, transaction or execution slot; resume reclaims authority and revalidates the exact intent. Unavailable policy/approval/audit database means zero calls. Crash before dispatch and after remote effect/before result commit leaves recoverable evidence; unresolved effects are not blindly retried. |
| MCP-08 | Verified idempotency/reconciliation prevents duplicate effects across lost responses, lease expiry, cancellation, replay and restore; unsupported providers require human resolution. |
| MCP-09 | Audit captures allowed/denied/approval/unknown paths with correlation and redaction; agents cannot edit past records or read another scope. Revocation blocks queued/retried calls. Controlled-clock tests cover 365/7/14-day boundaries, unresolved/hold protection, the 24-hour alert, daily cleanup and deletion reapplication after restore. |
| MCP-10 | Isolated SDK/server interoperability and cloud recovery demonstrate MCP works with MacBook off; RabbitMQ still carries internal events. No personal folders or legacy imports are required. |

## Alternatives and consequences

Direct per-agent clients are rejected because they duplicate policy, leak credentials and fragment audit. MCP as an event bus is rejected: tool calls cannot replace durable outbox/inbox and replay contracts. A single unrestricted server account with post-filtered outputs is rejected because it cannot establish source isolation.

The gateway concentrates policy and credentials, so its failure must fail closed and its service identity, egress and database privileges need independent security review. More bounded reads and explicit approvals reduce autonomy but make permissions reviewable. Native provider adapters remain valid when they provide better ingestion, permission or recovery semantics; Gmail watch/Pub/Sub is unchanged. No MCP server/provider is selected by this ADR.

## Unresolved implementation decisions

- Approve/revalidate the proposed SDK and protocol/transport profile, independent dependency lock and first synthetic/real server; compatibility/security evidence remains required.
- Gateway package/process placement, trusted runtime authentication and scoped credential storage/rotation; no new infrastructure is provisioned here.
- First tool/source/destination allowlist, data/effect classification, standing-grant/approval-expiry policy and validation of proposed limits, including control-request and non-ready storage bounds.
- Connector-specific idempotency/reconciliation evidence, approval of proposed retention, audit access/erasure/hold procedures and operational owner before production use.

## Protocol sources

Reviewed 12 November 2026. These sources describe MCP; the stricter permissions, approval and audit rules above are MarkOS design proposals, not guarantees supplied by the protocol.

- [MCP specification, revision 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28): protocol roles and negotiated capabilities.
- [MCP HTTP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization): transport authorization, distinct from MarkOS action approval.
- [MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices): token, proxy, discovery, egress and local-server risks.

- [Official Python SDK 2.3.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0): stable release, schema retry and OAuth timeout behavior.
- [SDK transport controls](https://py.sdk.modelcontextprotocol.io/client/transports/) and [protocol version selection](https://py.sdk.modelcontextprotocol.io/protocol-versions/): size/timeout coverage and pinning limitations.

Related: [[MarkOS v0.3 Specification]], [[MarkOS v0.3 Roadmap]], [[ADR-0002 GitHub as Canonical Project Record]], [[ADR-003 RabbitMQ Internal Event Bus]].


## M1-F synthetic implementation disposition

[[MarkOS v0.3 M1-F Governance and Synthetic Tools]] records the pinned mcp 2.3.0 / 2026-07-28 lower-level controlled wire path, actual SDK HTTPS JSON/request-SSE interoperability, exact scoped fixture grants/approvals, fresh fenced resume, audit/reservations, unknown outcomes and privileged retention. It permits one fixed synthetic read/attempt per run with no automatic tool retry; the ten-second parent run further restricts the general proposed deadlines above. Discovery/authentication has a shared four-operation budget and bounded two-request inspection. General tool and real-account flags remain false outside this explicit fixture profile.

The synthetic opaque credential is separately configured and bound to resource/owner/domain by the test server before data access; it is not a production OAuth adapter. Private resources/transfers, real provider tokens/audiences/accounts, consequential/paid effects and remote/cloud enablement remain unpassed. The acceptance table in the implementation record assigns exact synthetic evidence and remaining gates to every MCP-01–MCP-10 family; it does not claim full provider or M1 closeout. Scheduled cleanup/log/alert operations, process backup/restore, deletion reapplication, VM resource/proxy measurement and MacBook-off evidence remain M1-G/real-connector work.
