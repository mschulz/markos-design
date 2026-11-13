# Configuration authority

Existing `legacy.example.toml` belongs to the archived local Markdown/DuckDB application. Its `vault_path` spelling, and PR #7's proposed `markdown_root` rename, are legacy compatibility details. Neither configures the new runtime or implies PARA storage.

M0-A runtime schemas/examples now live under `config/runtime/`: synthetic event/subscription/policy JSON, explicit TOML and eight generated schemas (including the operational review reference). Preflight never discovers legacy config, note folders or the root package. Secret reference names are validated; secret values and connections are not resolved. See [[MarkOS v0.3 M0-A Contracts and Configuration]] for the fit review, trust boundary and limitations.

M0-B’s storage API takes an explicitly supplied DSN; preflight still does not resolve secret values, connect or run migrations. Database identity/scopes come from migration-managed authenticated login mappings, not a caller’s manifest alone. See [[MarkOS v0.3 M0-B Durable Storage]].

M0-C takes explicit broker/management endpoints and a reviewed publisher-user map at its library boundary, with no implicit secret discovery. Live topology audit credentials are separate from AMQP service identities. Example broker definitions contain no users/passwords. Preflight remains connection-free. See [[MarkOS v0.3 M0-C RabbitMQ Transport]].

No required `markdown_root` appears in the event foundation. A future Markdown export adapter may explicitly configure an ordinary output folder without PARA or Obsidian conventions. Originals and structured state remain in cloud source storage/PostgreSQL.

Never commit credentials, live DSNs, personal source data or secrets. See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] and [[MarkOS v0.3 M0 Implementation Plan]].

M0-D adds a strict synthetic schedule schema/example. Registration is explicit; preflight does not create or run schedules. Execution/scheduling permissions default false in the database and are separately provisioned by the migration/admin boundary. See [[MarkOS v0.3 M0-D Execution and Scheduling]].

M0-E adds the strict replay request/schema/example with effects suppression fixed true. Replay/cleanup require explicitly connected scoped libraries and separately provisioned capabilities. Synthetic source observations contain hashes/grants, no provider URLs or credentials. See [[MarkOS v0.3 M0-E Replay and Retention]].

M0-F adds strict `service.schema.json` and explicit test-only role examples under `runtime/lifecycle/`, plus `runtime/m0-closeout.json` as a review ledger with production readiness false. Only `serve-synthetic --confirm-synthetic --config` resolves its named test secrets and starts loops. Preflight remains connection-free. See [[MarkOS v0.3 M0-F Lifecycle and Closeout]].

M1-A adds 18 control schemas/examples (26 schemas total), `runtime/control/config.example.json` with every execution/real-account switch false and an intentionally empty/unusable JWKS placeholder. Explicit `preflight-control` does not read keys, authenticate or resolve secrets. Limits/identity descriptors are frozen test contracts, not applied middleware or database policy. See [[MarkOS v0.3 M1-A Contracts and Identity]].


## Merged M1-B and M1-C review status

PR #20 is merged. [[MarkOS v0.3 M1-C Authenticated Shared API]] records the `6a87fb0` main review, green 533-case post-merge CI and a separately reviewable authenticated shared API. It adds an explicit fixture-only ASGI factory, browser/CLI PKCE session protocols, current signature/upstream/local authority checks, scoped registry/configuration/status/activity operations, private health and additive migration 0007. A separate loopback HTTPS profile keeps real accounts, chat and tools disabled. API metadata is not runtime knowledge authority; PostgreSQL/RabbitMQ and MCP/M3 decisions are preserved. Interactive web/CLI clients remain M1-E; chat promotion remains M1-D. Provider integration, key custody, scheduled secret cleanup, API packaging/process/restore and production gates remain open. No merge or deployment.


## Merged M1-C and M1-D review status

PR #21 is merged. [[MarkOS v0.3 M1-D Durable Test Agent Conversation]] records the `8311f01` main review, six green post-merge checks and a separately reviewable durable synthetic conversation. The explicit conversation API profile adds atomic message/event/outbox admission, fixed checksum acknowledgement, durable reply/completion/activity, scoped polling/cursors, cancellation and current-authority/epoch/fence checks. It shares PostgreSQL/RabbitMQ and the two global execution slots with the unchanged no-op path; migration 0008 is additive. M1-C/preflight configurations remain execution-disabled. Real identity adapters, interactive clients/SSE, governance/tools, scheduled cleanup and packaged process/restore/production gates remain separate work. No merge or deployment.


## Merged M1-D and M1-E review status

PR #22 is merged. [[MarkOS v0.3 M1-E Web and CLI]] records the `4cb4d7b` main review, six green post-merge checks covering 706 tests, and the separately reviewable client slice. It fixes activity cursor-purpose binding and adds an explicit fixture-only web profile with five pages, a shared-HTTPS CLI, exact typed write previews, private explicit CLI session storage and durable scoped SSE with shared 1/4/20 lease limits, current-authority checks and restore-aware cursors. Additive migration 0009 preserves 0001–0008; existing dependency pins and archived work remain intact. Browser acceptance is required in CI. Approvals/tools remain disabled for M1-F; packaged process/restore/operational closeout remains M1-G. Real accounts and deployment remain disabled. Earlier slice sections below/above are chronological evidence; this section describes the latest review state. No merge or deployment.
