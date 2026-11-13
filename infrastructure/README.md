# New runtime infrastructure boundary

The accepted topology is DigitalOcean / Docker Compose with Supabase PostgreSQL and RabbitMQ as the internal event bus. Originals use cloud object storage (provider pending); no personal Markdown mount, PARA tree or DuckDB service is required.

M0-B adds the first ordered PostgreSQL migration and an explicit migration-role library API, exercised only against a dedicated synthetic local instance. See [[MarkOS v0.3 M0-B Durable Storage]]. No cloud SQL is applied. M0-C adds credential-free broker definitions, a version/digest profile and additive transport-state migration 0002, exercised on disposable CI services. Runtime identities have no configure permissions. M0-F adds the isolated lifecycle harness below; production backup/recovery/monitoring remain gates. See [[MarkOS v0.3 M0-C RabbitMQ Transport]]. Do not package or expose the archived local application as the cloud runtime.

See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[ADR-003 RabbitMQ Internal Event Bus]], [[MarkOS Cloud Deployment Architecture]] and [[MarkOS v0.3 M0 Implementation Plan]]. Exact operating profile, version/digest, provider cost and recovery evidence remain gates. This correction provisions and deploys nothing.

M0-D adds migration 0003 for task attempts/results/review, global lease slots and versioned schedule/occurrence/scan/control history. It preserves 0001/0002 checksums and requires explicit migration-role application. No cloud SQL or running service is configured. See [[MarkOS v0.3 M0-D Execution and Scheduling]].

M0-E adds migration 0004 for recovery capabilities, policy gates, replay permits/history, source observations/tombstones and explicit staged cleanup. `compose.m0-replay-test.yml` is a disposable CI volume-loss acceptance harness only. Its unique project labels guard broker-volume deletion; PostgreSQL survives the test and the owned project is removed afterward. It is not a production deployment or the M0-F lifecycle. See [[MarkOS v0.3 M0-E Replay and Retention]].

M0-F adds migration 0005 with generation-fenced claims/finalization, scoped heartbeats and private restore rotation; earlier migration bytes remain unchanged. `compose.m0-test.yml` adds nine synthetic roles using the independent runtime image, private health, resource/security bounds and generated test TLS. It is restricted to disposable hosted CI; its daemon-restart/pressure/restore controller is never packaged in the image. No production deployment is configured. See [[MarkOS v0.3 M0-F Lifecycle and Closeout]] and [[MarkOS v0.3 M0 Runbook]].

M1-B adds migration 0006 in a separate `markos_control` schema. Privileged bootstrap must first create the NOLOGIN `markos_control_gateway` group; the migration role creates no cluster roles or real login mapping. Its dedicated gateway LOGIN must be separately provisioned, non-admin, outside the M0 client group and explicitly allowlisted, with no direct table grants. The isolated test bootstrap supplies synthetic group/login mappings; no cloud SQL is applied. Migrations 0001–0005, RabbitMQ topology and existing runtime behavior are preserved. See [[MarkOS v0.3 M1-B Control Plane Persistence]].


## Merged M1-B and M1-C review status

PR #20 is merged. [[MarkOS v0.3 M1-C Authenticated Shared API]] records the `6a87fb0` main review, green 533-case post-merge CI and a separately reviewable authenticated shared API. It adds an explicit fixture-only ASGI factory, browser/CLI PKCE session protocols, current signature/upstream/local authority checks, scoped registry/configuration/status/activity operations, private health and additive migration 0007. A separate loopback HTTPS profile keeps real accounts, chat and tools disabled. API metadata is not runtime knowledge authority; PostgreSQL/RabbitMQ and MCP/M3 decisions are preserved. Interactive web/CLI clients remain M1-E; chat promotion remains M1-D. Provider integration, key custody, scheduled secret cleanup, API packaging/process/restore and production gates remain open. No merge or deployment.


## Merged M1-C and M1-D review status

PR #21 is merged. [[MarkOS v0.3 M1-D Durable Test Agent Conversation]] records the `8311f01` main review, six green post-merge checks and a separately reviewable durable synthetic conversation. The explicit conversation API profile adds atomic message/event/outbox admission, fixed checksum acknowledgement, durable reply/completion/activity, scoped polling/cursors, cancellation and current-authority/epoch/fence checks. It shares PostgreSQL/RabbitMQ and the two global execution slots with the unchanged no-op path; migration 0008 is additive. M1-C/preflight configurations remain execution-disabled. Real identity adapters, interactive clients/SSE, governance/tools, scheduled cleanup and packaged process/restore/production gates remain separate work. No merge or deployment.


## Merged M1-D and M1-E review status

PR #22 is merged. [[MarkOS v0.3 M1-E Web and CLI]] records the `4cb4d7b` main review, six green post-merge checks covering 706 tests, and the separately reviewable client slice. It fixes activity cursor-purpose binding and adds an explicit fixture-only web profile with five pages, a shared-HTTPS CLI, exact typed write previews, private explicit CLI session storage and durable scoped SSE with shared 1/4/20 lease limits, current-authority checks and restore-aware cursors. Additive migration 0009 preserves 0001–0008; existing dependency pins and archived work remain intact. Browser acceptance is required in CI. Approvals/tools remain disabled for M1-F; packaged process/restore/operational closeout remains M1-G. Real accounts and deployment remain disabled. Earlier slice sections below/above are chronological evidence; this section describes the latest review state. No merge or deployment.


## Merged M1-E and M1-F review status

PR #23 is merged. [[MarkOS v0.3 M1-F Governance and Synthetic Tools]] records the `231a018` main review, exact preserved M1-E tree and six green post-merge checks with 770 cases. The separately reviewable M1-F slice adds an explicit synthetic governance profile, fixed reviewed fixture grants, immutable exact approvals, slot-free durable waiting/fresh execution leases, atomic one-time dispatch/audit/reservations, bounded pinned MCP 2.3.0 / 2026-07-28 HTTPS reads and unknown-outcome reconciliation. Additive migration 0010 preserves 0001–0009; shared limits, scoped typed web/CLI controls and explicit privileged retention/tombstones are covered. Ordinary no-tool chat and archived code remain intact. Real accounts/OAuth/effects and deployment stay disabled. M1-G owns packaged processes, full restore/maintenance/operational closeout; earlier slice sections are chronological evidence. No merge or deployment.
