# Decisions and evidence

Why the rules in `CLAUDE.md` exist. Each rule there links to a heading here with
"→ decisions: <heading>".

**Read this before you relax a rule.** Most entries protect an invariant that the
code, migrations or tests depend on. A rule that looks like ceremony usually
stops lost work, duplicate effects or a cross-owner leak. The reason is recorded
here so you can check it rather than guess.

This file replaces the old ADRs and M0/M1 milestone records. It keeps decisions
that still govern the code and drops status, PR and test-count history. Where a
later milestone changed a rule, only the current rule appears.

---

# Architecture

## v0.2 is abandoned; v0.3 is an independent event-driven runtime

The owner abandoned the original design on 12 November 2026. It stored knowledge
as canonical Markdown in a PARA tree, used DuckDB and Obsidian, and ran on the
desktop. Do not reintroduce any of that, and do not port code, schemas, models
or locks from the old `markos` package or repository.

Requirements carried over, not code: human approval, provenance, owner/domain
isolation, privacy, outbox/inbox, idempotency, durable tasks and recovery. An
old test for one of these proves nothing about the new runtime.

## One project at the repo root: package `markos_runtime`

All code lives in `src/markos_runtime/` with one `pyproject.toml`, `uv.lock`,
`tests/` and Dockerfiles at the repo root. It must install and run without
personal folders or legacy config. Python is pinned to `>=3.13,<3.14`; widening
it needs test evidence. The base install is pydantic, psycopg and aio-pika only. The
`control` and `tools` extras and the `browser` dev group stay out of the base
wheel and image. Add modules here; do not add a second backend project or lock.

## RabbitMQ is the internal event bus, not the record

RabbitMQ over AMQP 0-9-1 carries internal events. It replaced provisional MQTT.
MarkOS needs independently buffered subscribers, competing workers per queue,
explicit durable handoff and inspectable failures. RabbitMQ's exchange/queue
model fits that directly. MQTT delivery acknowledgement is not a business handoff.

PostgreSQL stays the authority. The broker can lose confirmed messages (volume
loss, restore), so nothing may treat broker state as the archive.

Rejected: MQTT internally (less direct for per-service queues; may return only as
a device adapter). PostgreSQL polling only (the design needs routed async
distribution). RabbitMQ as archive or database (cannot support recovery).
Streams, a managed broker or a cluster now (deferred on cost).

Google Cloud Pub/Sub still delivers Gmail notifications. That is separate
ingress, not the internal bus.

## Topology is fixed and provisioned by an administrator

- One durable topic exchange per domain: `markos.{domain}.events`.
- Routing keys `v1.{category}.{event}`, e.g. `v1.control.chat-requested`.
- Queues `markos.{domain}.{service}`: durable, classic, non-exclusive, no
  auto-delete, no TTL or expiry, `overflow=reject-publish`.
- Vhosts are owner-scoped. Same queue name never means two owners share it.

Runtime identities have empty configure permissions and never declare exchanges,
queues or bindings. An administrator applies `infrastructure/rabbitmq/`.
Registering an agent never creates a queue.

Before publishing, a separate read-only management identity audits live topology.
The exact subscriber set and queue properties must match. Mandatory routing alone
passes if any one queue matches, so it cannot prove every subscriber exists.
Unknown routes, wildcards and exchange-to-exchange bindings fail closed.

## Production runs in the cloud and must survive the MacBook being off

Target: one DigitalOcean VM running Docker Compose, with Supabase PostgreSQL.
The VM hosts broker, ingestion, workers, scheduler, notifications and web API.
The MacBook is only a dev and admin client. Gmail ingestion, agent runs and
notifications must run while personal devices sleep.

So schedules and missed-run recovery are database-backed. Container cron,
in-memory timers or MacBook jobs can never be the only scheduler. Every
long-running service needs a restart policy, health checks, dependency retry and
graceful shutdown. One VM is not highly available.

Rejected: MacBook-hosted or hybrid production. Deferred: multiple VMs or a
cluster. Nothing is provisioned yet (see Open questions).

## GitHub is the project record; runtime data and secrets never enter it

`mschulz/markos-design` holds code, tests, infrastructure, config schemas,
examples and docs. Drafts elsewhere never override it. Never commit email, bills,
attachments, personal knowledge, databases, credentials, tokens, live DSNs or
session files. Examples are synthetic. Secrets are referenced by name only.

# Storage and data

## PostgreSQL owns durable state; Markdown is only an export

PostgreSQL holds events, outbox/inbox, tasks, schedules, leases, approvals,
conversations, audit and structured knowledge. Immutable originals live in
access-controlled object storage, referenced by checksum.

Generated Markdown is a derived export in an ordinary folder, never the
authority. `markdown_root` is not a startup setting. Fixture stores (synthetic
issuer, MCP ledger) stand in for external systems and never replace PostgreSQL.

## An event and its outbox entry commit in one transaction

A producer commits its domain change, the immutable event (canonical bytes and
body hash), the intended-consumer snapshot and the outbox entry together. Any
failure rolls all of it back. The consumer snapshot is frozen, so later grant
changes do not alter who receives an old event. Same ID and body is a duplicate.
Same ID with a changed body goes to quarantine and never alters the original.

## Publication counts only after a positive broker confirm

Publish persistent, mandatory messages on a confirm channel. Record publication
only after an actual `Basic.Ack` with no return. A nack, return, confirm timeout
or unknown connection outcome keeps the entry pending. Retry with the original
event ID and jittered backoff (1 s to 5 min). A timeout is unknown, not rejected.

Publish the exact committed bytes. Re-serializing would change the checksum the
consumer verifies.

## Consumers commit before they acknowledge

Commit the inbox identity `(consumer, owner_id, domain, event_id)` and a task or
review record, then ack manually. A crash between the two causes a redelivery
that the inbox deduplicates. Poison, expired or unauthorized deliveries are acked
only after a scoped review record commits. Uncertain failures leave the delivery
unacked and close the channel.

Never use an immediate nack/requeue loop. Retries use the database scheduler with
bounded backoff and attempt limits. Broker dead-lettering is not lossless, so the
PostgreSQL quarantine record is authoritative.

## Long work runs from database tasks, never inside a broker delivery

Agent work, model calls and tool calls run from durable tasks after handoff. No
broker delivery, transaction or connection is held across execution or any
network call. Claim transactions commit before HTTP or AMQP calls start. Holding
a delivery during long work trips deadlines, blocks prefetch and overlaps
redelivery with running work.

Classic queues have no broker ack timeout, so the consumer runs watchdogs: 10 s
active handoff, 30 s total age including semaphore wait. On timeout it closes the
channel (2 s budget), then aborts the socket.

## Delivery is at-least-once; operation keys stop duplicate effects

Confirms and acks never give exactly-once effects. Every event carries an
operation key, and tasks are unique per scoped operation. A changed payload under
the same key is a conflict. Ordering across workers is not guaranteed. Where it
matters, use database sequences (e.g. per conversation). Timestamps never order.

## Retained database state drives recovery; replay suppresses effects

Reconciliation scans retained dispatch state, including events already confirmed,
and requeues missing handoffs with original IDs. Broker storage loss can delete
confirmed work. A CI drill deletes the broker volume, keeps PostgreSQL and proves
each subscriber still gets exactly one result. Replay is rate-limited across
replicas (10 events/s, 10 per pass, 60 s probe delay).

Replay fixes `suppress_effects=true`, enforced again in SQL. Events older than 30
days go to durable review instead of running. A reviewer may issue a one-use
permit (default 5 min, max 1 h) bound to event, body hash, publisher, scope and
consumer set. A permit never overrides deletion, expiry or source-access checks.

## Operational events: 30-day replay, 90-day dedup floor, unfinished work held

- Keep replayable payloads for 30 days from creation and 30 days after all
  consumers are terminal, whichever is later.
- Keep minimal inbox and operation identity 90 days after the last terminal
  transition or attempt.
- Never age-delete unpublished events, unfinished tasks, unresolved quarantine or
  their source bytes. Alert and pause ingress instead.
- Cleanup is explicit, bounded and defaults to dry run. No daily service runs it.

This is a replay-safety floor, not a privacy policy, and is separate from tool
audit retention.

## Deletion beats retention and leaves only a tombstone

Privacy deletion removes payload bytes at once, even for held work, and keeps a
minimal tombstone that blocks replay and reimport. Related leases are fenced, so
stale completions and queued deliveries cannot recreate work. Never resurrect
deleted bytes from a backup. A restore of an older snapshot must reapply current
deletion records before real access. Known gap: an in-flight broker copy may hold
old bytes until consumed. Broker, object and backup erasure are not built.

## Real data: email stays in Gmail, bill originals stay in MarkOS

Owner decisions, 2026-11-27:

- **Email is kept as a pointer only.** After processing, the email stays in
  Gmail. MarkOS keeps the mail record: Gmail message ID, thread ID, history ID,
  received time, labels and status. It never stores mail bodies, attachment
  names or other attachments. If the email is later deleted in Gmail, the
  pointer stops working, and MarkOS keeps the record.
- **Bill PDFs are kept as originals.** A bill PDF imported from the bill label
  is stored unchanged in object storage, with its SHA-256 and the Gmail
  pointer. Cited answers and later re-extraction depend on the original, and
  Gmail access can be revoked or the email deleted.
- **Records stay until the owner deletes them.** Disconnecting the mailbox
  deletes the token only.
- **Deleting a bill** removes its PDF object, extracted facts and corrections
  at once. A tombstone keeps only the SHA-256 and the Gmail pointer, so a later
  label import or resync does not import the bill again.
- **Deleting a mail record** removes its labels and attachment references. A
  tombstone keeps the Gmail message ID, so a resync does not store it again.
- **Backups:** owner decision, 2026-11-28: the database relies on Supabase Pro's
  7 daily backups, and Spaces originals have no backup. Each tombstone is also
  logged to Spaces, and a restore replays that log (M7-G). The earlier plan was
  7 daily and 4 weekly encrypted snapshots. Deleted rows leave Supabase's
  backups after at most 7 days. A restore reapplies tombstones before real
  access, as above.
- **Logs:** 14 days. Logs never hold mail content or bill values.
- **Audit:** minimal audit metadata kept 365 days, with no mail content.

Not built: bill and mail-record deletion, object erasure and backup expiry.

## Migrations are append-only and checksummed

`infrastructure/migrations/NNNN_name.sql`: 0001–0024 exist; next is 0025.

- Never edit an applied migration. The ledger stores SQL checksums, and a changed
  file blocks migration and API readiness.
- Every change is a new additive migration. No down migrations.
- The runner takes an advisory lock, requires contiguous names and commits DDL
  with the ledger. Failure rolls the migration back.
- API readiness compares applied versions and checksums with packaged
  fingerprints. A mismatch keeps the API unready; `/live` stays up.
- Test each migration against a populated previous-version database, twice.

## Runtime processes never run migrations

Only the explicit migration-role API (`store/migrations.py: migrate`) applies
SQL. Preflight, workers, the gateway and `serve-*` commands never migrate, create
roles or create topology. Runtime logins must not hold schema-owner rights, and a
process that migrates on start can hit the wrong database or race a sibling.
Privileged bootstrap creates the NOLOGIN groups (`markos_runtime_client`,
`markos_control_gateway`) before migrating. Migrations create no logins.

## Database time is the only clock

Leases, expiry, schedules, retention and age gates use PostgreSQL time. Each
storage transaction sets its timezone to UTC. No runtime API accepts a caller
clock. Only isolated tests, via the migration role, may replace the private
policy clock. Never add a runtime time-travel setting.

## Schedules are versioned, durable and explicit about missed runs

Two kinds: elapsed UTC intervals (60 s to 31 days) and one daily wall-clock time
with a stored DST fold choice and `shift_forward` gap policy. No arbitrary cron.
Each schedule names its missed-run policy: `catch_up` (bounded, keeps a debt
cursor), `coalesce` (one latest run) or `skip` (short grace). All are audited.
Occurrence identity includes schedule ID, version and due instant, so scans are
idempotent. A changed definition needs exactly the next version. A scan commits
occurrences, events, outbox and cursor together and makes no network call.

# Identity and access

## Owner and domain come from authenticated identity, never from input

Everything is scoped by `owner_id` (non-nil UUID) and `domain`, taken from the
authenticated database login or the verified human session. Never accept owner,
domain, actor, role or service identity from a body, query, cursor, header, model
output or `SET`/GUC variable. A requested scope is checked against current
membership, never trusted. Transport names and post-read filtering do not isolate.

Reads filter scope before pagination and counts. An object in another scope
returns the same 404 as a missing one.

## Runtime logins get least privilege; writes go through definer functions

Each service has its own login, mapped by migration to one service/owner/domain.
`session_user` must be that login. A shared pool or admin login is no substitute.

- RLS-filtered reads and EXECUTE on narrow functions only. No direct DML, DDL,
  grants or registry access.
- SECURITY DEFINER functions set a fixed `search_path`, qualify names and have no
  public EXECUTE.
- Start-up rejects superuser, BYPASSRLS, CREATEROLE and schema-owner logins.
- Capabilities (`can_execute`, `can_schedule`, `can_replay`, ...) default false.
- Broker publishers have no impersonator tag. Consumers check the broker-validated
  `user_id` against a reviewed map.

## The gateway is a trusted delegation service

Humans never get database credentials. The gateway authenticates a person, then
calls narrow `markos_control` functions with that actor. Every function rechecks
current session, membership, role, scope and revision. The gateway login is
EXECUTE-only, listed in private `gateway_logins`: no tables, migrations or broker
admin.

Accepted risk: whoever holds the gateway credential can impersonate a provisioned
subject at that boundary. Narrow functions, immutable audit and separate runtime
credentials reduce this but do not remove it. Neither PostgreSQL nor RLS verifies
JWTs here.

## Human identity is checked against current authority on every request

Tokens use issuer, audience, ES256 and token kind from configuration. Never take
a key URL, issuer or algorithm from the token. Unknown keys fail closed after one
bounded refresh. Each protected request and dispatch rechecks the upstream
session plus local session, principal and membership. A signed JWT or cached role
never bypasses this. Logout and revocation block the next operation. Streams
recheck at least every 15 s. Unavailable authority fails closed.

The issuer today is a synthetic fixture. In production the MarkOS issuer signs
the person in with Google, then mints its own ES256 tokens (owner decision,
2026-11-27, `docs/m7-production.md`). Only the issuer checks Google's RS256 ID
token. A configured Google subject ID maps to a MarkOS subject; an email alone
never grants access. Accepted risk: a compromised Google account can sign in
until the owner removes its mapping or membership. The account uses 2-Step
Verification.

## Roles are additive and nonhierarchical

| Capability | Viewer | Operator | Approver | Administrator |
|---|---|---|---|---|
| Inspect scoped status and history | yes | yes | yes | yes |
| Send chat, import bills, cancel own runs | no | yes | no | no |
| Decide exact approval intents | no | no | yes | no |
| Change registry, configuration, grants | no | no | no | yes |

Administrator does not imply approver or operator. No role can add itself to a
scope, approve an agent's request or turn a denied grant into an allowed call.
Hidden buttons, digests and status hints confer no authority.

## Browser and CLI sessions are opaque, short and server-side

- Browser: opaque `__Host-markos-session` cookie (Secure, HttpOnly, SameSite=Lax,
  Path=/, no Domain), rotated at login. Writes need CSRF proof and exact Origin.
- Store only an HMAC lookup of the secret. Encrypt provider tokens and PKCE
  verifiers. Provider tokens never reach the browser.
- 30 min idle, 8 h absolute (refresh never extends it), 10 per principal, 100
  globally.
- CLI: PKCE onboarding, opaque bearer of at most 5 min, kept in an explicit 0600
  file in a 0700 folder outside any Git checkout.
- Tokens never go in URLs, HTML, logs, shell arguments or browser storage.

## Restore rotates the generation before any writer starts

Lease-sensitive operations need a numeric fence and the current database
generation. After a restore, the migration role calls
`markos_runtime.fence_restored_state()` before any writer starts. That
invalidates leases, sessions, login flows, unconsumed approvals and replay
permits. Services exit when the generation changes rather than adopt it.

After restoring an older database, numeric fences can repeat, so a stale worker
could otherwise finalize work it no longer owns. Limit: this fences the restored
database only. It does not revoke credentials, fence a surviving old database or
stop a provider.

## Leases are fenced, and two execution slots are shared database-wide

A claim increments the fence and records executor and attempt in one short
transaction. A stale fence, wrong executor, expired lease or old generation
cannot renew or finalize. Completion writes the result, ends the attempt and
frees the slot in one commit. A unique result constraint rejects late duplicates.

Two slot rows cap valid execution leases at two across all services, owners,
domains and replicas, not per process. Chat, governed chat and no-op work share
them. Tool calls use a separate pair. The target is one shared VM, and
per-process counters cannot bound global load.

## Shared limits are enforced in PostgreSQL across replicas

Admission limits are database transactions. Excess is refused or deferred with a
durable record. Accepted work is never evicted. Fixed values include: 64 KiB HTTP
and event body, 16 KiB chat text, 256 KiB response, 100 rows per page, 30
writes/min per principal (burst 5), 16 short requests across gateways, SSE
1/session, 4/principal, 20 global, 100 pending chat runs (10 per scope), 5 s
statement and 1 s lock timeouts, pool of four. `ControlLimits` freezes these.
Changing one is a contract change with exact and one-over tests.

## Explicit config objects gate every route and capability

Each capability has a closed config class built explicitly in code:
`SyntheticApiConfig`, `SyntheticConversationApiConfig`, `SyntheticWebApiConfig`,
`SyntheticGovernanceApiConfig`, `SyntheticBillApiConfig`. Routes exist only for
the constructed profile. No request, registry entry or config revision can pick a
profile, enable execution or load code. `tool_execution_enabled` and
`real_accounts_enabled` are fixed false.

Control preflight (`preflight-control`) only validates: no sockets, secrets,
discovery or execution. It reads only the file given by `--config`.

## Clients use the authenticated HTTPS API only

Web and CLI call the same `/api/v1` routes and authorization code. The CLI has no
SQL or broker path. No browser subscribes to RabbitMQ. A 202 means durably
stored, not completed. Writes never retry automatically. Registry, config, grant
and decision writes need the digest of an exact preview, which is not
authentication. Render all user, agent and tool text as plain text.

# Agents and tools

## Event types are a closed union; chat carries references, not text

The wire parser accepts only the no-op envelope and typed chat request and
completion. A new event type means extending contract, SQL constructors, routes,
topology and executor together. Chat events carry a scoped message reference and
checksum, never text, credentials or tool intents. Broker messages carry bounded
metadata and references. No generic handler or plugin registry: executors are
fixed library operations.

## Agents reach tools only through the governed gateway

Agents submit typed call intents. They never open connections, launch servers,
hold credentials or pick endpoints. Broker subscriptions grant no tool access.
ChatGPT, Codex or Claude connector permissions grant no MarkOS access.

Rejected: per-agent MCP clients (duplicate policy, leak credentials, fragment
audit). MCP as event bus (cannot replace outbox and replay). One unrestricted
server account with filtered output (cannot isolate sources). Native adapters
such as Gmail Pub/Sub and classifiers stay valid without MCP.

## Tool grants deny by default and bind exact scope

A grant binds owner, domain, agent, server and endpoint, tool, input-schema
digest, data class, recipient, effect and budget. Missing or unclassifiable
fields deny. Discovery never enables a tool, and annotations are untrusted. A
changed tool, schema, endpoint or capability disables the grant and invalidates
pending approvals. A tool server that gives no response (refused, reset or
timed out) keeps the grant: the approved call is deferred and retried, and it
still needs a successful check before dispatch. A TLS failure or any wrong
answer disables the grant. A result cannot request another call, and a returned URL
authorizes nothing. Classify disclosure as well as effect: a read can leak
private arguments.

## Approval binds one immutable intent and is consumed once at dispatch

The intent binds scope, requester, task/run/epoch/fence, agent, config, grant and
policy versions, tool and schema, canonical argument and preview digests,
recipient, effect, data class, expiry (max 15 min) and one attempt. Canonical
form v1 is sorted-key compact UTF-8 JSON.

Only a current scoped approver decides. Agents cannot. The Approve click records
a decision and never dispatches. Dispatch rechecks everything, consumes the
approval once and commits dispatch record and audit before the call. Any change
needs a new intent. M0 review resolution never approves an effect.

## Waiting for approval holds no slot, transaction or delivery

On suspension the worker records the intent and releases slot, lease and
transaction. A later worker claims fresh authority. The intent's `task_fence`
fixes its suspension generation. A private `resume_fence` links it to each fresh
lease, and dispatch checks both. A capacity race defers the task and frees the
slot without consuming the approval or counting an attempt.

## Unknown tool outcomes are never retried automatically

A lost response, cancellation, stale lease, restore or failed result write leaves
the dispatch unknown, with its outcome capacity still reserved. It is never
called again automatically, because a local fence cannot roll back a remote
effect. The live profile allows one call and one attempt per run. An approver may
record a human reconciliation finding, which frees capacity and ends the run.

## Tool audit is append-only, written first, and pruned by explicit cleanup

Persist every request, denial, approval, dispatch and outcome. Commit the dispatch
record before credentials reach the call. If policy, approval or audit storage is
down, make zero calls. Audit holds IDs, hashes, versions, scope and outcome, never
credentials or full payloads. No runtime or gateway role can rewrite it.

`cleanup_tools()` is explicit, bounded and migration-role-only. It removes
optional argument and result copies 7 days after terminal state, and terminal
metadata after 365 days, leaving hashed tombstones that block re-admission.
Unresolved outcomes, active work and holds block cleanup. It never deletes
originals or conversations. These values are synthetic, not a privacy policy.

## The MCP transport is pinned, loopback-only and fixture-only

The `tools` extra pins `mcp==2.3.0`, protocol `2026-07-28`. The adapter uses a
controlled low-level HTTP path. SDK auto-negotiation, `HeaderMismatch` retry,
interactive OAuth and implicit credentials are off because they could bypass
grant, schema, approval or attempt checks.

Only `fixture.read` at `https://127.0.0.1:PORT/mcp` with an explicit CA. No DNS,
redirect, proxy or discovery. Bounds: 64 KiB request, 1 MiB total response, 5 s
connect, 10 s idle, inside the 10 s parent run. Compressed responses are refused.
Prompts, resources, sampling, elicitation, roots, tasks, stdio and paid or
consequential tools are off. The fixture credential is not OAuth, and a human
bearer is never forwarded.

## The test agent is deterministic and has no model or tools

`control-test-agent` replies `Synthetic acknowledgement: ` plus the message
checksum, checked in SQL. It never reads or echoes the message. Handler failure
is terminal with zero retries. Crash recovery may reclaim the same operation up
to three fenced attempts. Registering or revising an agent enables nothing. A new
revision resets it to disabled until configuration is re-reviewed.

## Bill answers are deterministic, cited and uncertain when unsure

K1-A, the current priority ahead of Gmail and classification, imports pasted bill
text, keeps the original, extracts facts and answers fixed questions. No LLM, PDF
or OCR. Every figure cites its source line. Missing, malformed or conflicting
values show "Needs review" and never become confident answers. Currency is AUD
only if the text says `Currency: AUD`.

Originals go to the `BillObjects` local object adapter: private owned directory,
server-built paths, no symlinks, immutable writes, digest checks, 100 per scope.
Facts and provenance live in PostgreSQL (0014). Import needs operator. Only
`SyntheticBillApiConfig` plus a gateway-only `bill_objects_path` enables it.

## Cancellation reasons are recorded once and never rewritten

A cancelled approval records its reason at that moment. Explanations use locked,
eligible request IDs, not a timestamp window. A window let a concurrent failure
overwrite another request's reason. Never reactivate a cancelled request to
improve its display. Signing out the sender cancels their pending requests on
purpose.

# Operations and testing

## Tests use disposable databases and brokers only

Integration tests need an explicit `MARKOS_TEST_ADMIN_DSN` for a dedicated
disposable cluster. They create and drop only `markos_test_*` databases and their
own roles. Broker tests need disposable local RabbitMQ. Never point tests at a
shared, live or preview database: the fixtures drop databases and install roles.

## CI fails on missing services instead of skipping

Locally a missing service skips its cases. CI sets the `MARKOS_REQUIRE_*` flags
(`DB_TESTS`, `RABBIT_TESTS`, `API_TESTS`, `BROWSER_TESTS`, `TOOLS_TESTS`,
`VOLUME_TEST`, `LIFECYCLE_TEST`, `CONTROL_PROCESS`), so a missing service fails.
A skip is never evidence. Keep every required job: base lint/types/schemas,
PostgreSQL and broker suites, volume-loss and lifecycle drills, standalone wheel,
API/browser suite and the packaged-preview job.

## Destructive drills run only on owned, labelled, ephemeral projects

Daemon restarts, volume deletion and kill drills run only on GitHub-hosted
runners against uniquely named, labelled Compose projects. Helpers check the
local Docker socket and labels. Never bypass them or run drills on a shared,
personal or production host. The fault controller stays out of every image.

## Everything runs synthetic; real accounts, providers and effects are off

Profiles use synthetic data, fixture issuers and fixture tools. Service commands
require `--confirm-synthetic`.

Owner decision, 2026-11-27 (M7-D2): one exception. `serve-control-production
--confirm-production` runs a service under `control-process-production-v1`. That
profile signs in only with Google, keeps tools and fixture paths off, and leaves
mail off until the owner turns it on. It never accepts `--confirm-synthetic`, and
the synthetic commands never accept it. Running it does not authorize real data:
the gates below still apply.

Off until separately reviewed: real identity,
accounts, Gmail, models, real MCP connectors, paid or consequential tools,
personal data and deployment. MarkOS is not production ready. These gates must
pass before production:

- real source provider permission, deletion and backup erasure
- provider plans, region, cost, production secrets and TLS
- host capacity, VM boot, measured RPO/RTO and restore owner
- encrypted off-host backup and independently placed monitoring
- real action approval expiry and unknown-effect reconciliation
- MCP-01 to MCP-10 before enabling any real MCP connector

Accepted architecture is not deployment authorization.

## Containers are minimal and health is private

Images hold only the independent project: no archive, tests, migration role,
dumps or credentials. Containers run non-root, read-only, capability-free, with
PID and file limits and no Docker socket. `/live`, `/ready` and `/metrics` are
loopback-only. A 45 s watchdog exits a stalled process. SIGTERM drops readiness,
then drains within a hard deadline.

## Logs and messages never carry secrets or payloads

Never log environment variables, connection strings, tokens, source bodies, chat
text or raw exceptions. Telemetry uses fixed codes and bounded counters. Errors
use fixed codes and a random request ID. Never disable TLS verification to fix a
failure. Remote AMQP and management need verified TLS. Plain connections are
allowed only on loopback in tests.

## Lean delivery: runnable feedback first, real gates before real data

Ship small runnable synthetic slices and collect feedback before widening
infrastructure. Do not build maintenance or restore frameworks just to call a
slice complete. Do not block synthetic feedback on production-only gates. Do not
waive a safety gate before real accounts or data. Automate approval regression
tests rather than asking the owner to run them.

---

## Open questions

Proposed in the sources and never accepted or measured. Treat as starting values.

- **Production broker profile.** RabbitMQ 4.4.0 (digest-pinned) and aio-pika
  10.2.3 are tested. Unmeasured: 4 vCPU / 8 GiB VM, 4 GiB broker cap, 2 GiB
  memory and 10 GiB disk alarms, 10,000 msg / 32 MiB queues, 64 queues, 32
  connections. Quorum queues wait for a multi-node topology.
- **General MCP profile.** 512 MiB / 0.5 CPU, 10 attempts/min per scope, 60 s
  budget, one retry for safe reads. The live profile is stricter.
- **API process ceiling.** 256 MiB / 0.5 CPU, not enforced outside test Compose.
- **Real-data retention.** Decided (see "Real data: email stays in Gmail, bill
  originals stay in MarkOS"). Still proposed: 24 h unresolved alert, daily
  cleanup, 24 h RPO.
- **Object storage provider** and coordinated object/database backup and deletion.
- **Production operations.** Region, sizing, cost, monitoring, alert route, VM
  boot and MacBook-off evidence.
- **Local stdio MCP servers.** Deferred. Would need a sandbox with pinned
  executables and no ambient credentials or home mounts.
- **Tool governance and human identity designs** were never formally accepted,
  although M1 built their synthetic subset. The rules above describe what the
  code enforces; production versions need a fresh review.
