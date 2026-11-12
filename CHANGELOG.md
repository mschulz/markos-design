# Changelog

All notable changes to the MarkOS Design repository will be documented in this file.

The format is inspired by *Keep a Changelog*, but organised around design milestones rather than software releases.

---

## Unreleased

### M0-D synthetic execution and scheduling — 2026-11-12

- Reviewed PR #12 at `22fbf25`: no blocking correctness/architecture findings; 194-case CI green.
- Added explicit fixed no-op execution with PostgreSQL leases/fences/global two-slot limit, attempts, retry and durable review; hard process recovery and commit-failure tests.
- Added strict versioned interval/daily schedules, DST/missed policies, atomic occurrences/events/outbox/cursor and immutable scan/control history; additive migration 0003 preserves prior checksums.
- Required suite: 256 tests, including 75 PostgreSQL and 12 broker/database cases. Real approvals, agents/tools, replay/retention and service lifecycle remain gates. No merge or deployment.

### M0-C RabbitMQ transport prepared for review — 12 November 2026

- Reviewed PR #11’s transaction/identity/migration boundaries and passing CI. Added pinned, independent RabbitMQ transport, live topology/validated identity checks, actual confirms/returns, durable retry/review state, manual ack after commit and independent watchdog/generation recovery.
- Added additive migration 0002, operational review schema, credential-free broker definitions/profile and required real-broker/database CI. The suite has 194 cases; local RabbitMQ absence skips 11 explicitly. See [[MarkOS v0.3 M0-C RabbitMQ Transport]]. No agent worker, full replay, merge, cloud provisioning or deployment.

### M0-B durable storage prepared for review — 12 November 2026

- Reviewed PRs #9/#10 for the independent architecture; corrected PR #10’s invalid CI runner context, and its corrected hosted workflow passes.
- Added explicit PostgreSQL storage, ordered checksummed migration, login-bound roles/RLS, atomic event/outbox and inbox/task writes, dedup/conflict quarantine and fenced claims. 145 independent tests include 34 real PostgreSQL cases. See [[MarkOS v0.3 M0-B Durable Storage]]. No RabbitMQ adapter, worker execution, merge, cloud SQL or deployment.

### M0-A implementation prepared for review — 12 November 2026

- Prepared independent M0-A contracts/configuration for review in services/runtime, with a fresh minimal lock, validation-only CLI, synthetic schemas/examples and scoped CI. 104 independent tests and clean-wheel isolation checks pass. Source/manifest trust and unimplemented database/broker guarantees are explicit; see [[MarkOS v0.3 M0-A Contracts and Configuration]]. Archived source, root package/lock and personal data remain unchanged. No merge or deployment.

### Completed on 12 November 2026


- Corrected the architecture baseline after the owner's explicit abandonment of the original design. ADR-004 supersedes PARA/Markdown-canonical/DuckDB/desktop assumptions and legacy-code reuse; RabbitMQ is selected internally and PostgreSQL/source objects own durable state.
- Updated Vision, specification, roadmap, M0 plan, active contributor/boundary docs and authority labels. Proposed an independent runtime project and acceptance matrix; marked old designs/evaluations/imports as historical. Exact v0.2 snapshots, source code, dependency lock and import manifest remain unchanged.
- PR #7's naming cleanup and PR #6's contracts are prior work, not the replacement foundation. No runtime, source migration, merge or deployment is performed by this documentation correction.

Earlier entries below describe work at the time; they do not reinstate superseded plans.


- Prepared M0.0 as a two-parent history-preserving import from markos d5d46cb,
  stacked on PR #3. Source application/tests and uv.lock remain byte-identical;
  source README/AGENTS are preserved as historical documents, active machine
  paths removed, and canonical instructions/ignore rules reconciled.
- Verified the original 143-test suite and the imported 145-test suite plus
  lock, Ruff and strict mypy gates. Added import/config safety guards and a
  source/destination hash manifest; see the M0.0 Import Record. Review/merge
  remain pending; no real-vault access, runtime implementation or deployment.

- Reviewed PR #3 and inspected markos main d5d46cb: Python 3.13/uv with a
  read-only knowledge stack and tests, but no event/task/PostgreSQL runtime.
- Added the proposed v0.3 M0 implementation plan, history-preserving import,
  concrete source/schema changes, M0.0–M0.7 slices and A01–A20 acceptance matrix.
- Closed two design gaps: required owner_id in event/inbox/business scope, and
  application handoff watchdog/channel-close recovery for classic consumers.
  Updated ADR-003 (1.2), specification (0.3.4), roadmap (1.4), cloud architecture
  (1.3), navigation and future test guidance. No runtime/import/deployment or
  test execution occurred; this records documentation review and planning only.

- Prepared a follow-up to merged PR #2 with proposed RabbitMQ 4.4.0, aio-pika 10.2.3 and durable classic
  queues, concrete single-VM resource/flow guardrails, 30-day routine replay and
  90-day deduplication floors with unfinished-work holds and deletion gates.
- Updated ADR-003 (1.1), specification (0.3.3), roadmap (1.3), cloud architecture
  (1.2) and future test requirements. Sizing remains unmeasured and subject to
  approval; no limits/retention applied, dependencies installed or deployment run.

- Proposed ADR-003 RabbitMQ Internal Event Bus and updated v0.3 specification
  (0.3.2), roadmap (1.2) and cloud architecture (1.1) for AMQP routing, durable
  handoff, confirms/acks, bounded retries and broker-storage-loss replay.
- Preserved accepted DigitalOcean/Compose/Supabase placement; MQTT is optional
  for device adapters and Google Cloud Pub/Sub remains Gmail ingress.
- Added future M0 broker acceptance scenarios and navigation/project-memory
  references. Runtime tests and milestones remain unstarted; no provisioning,
  deployment, Space or vault changes, or merge are included.

### Accepted on 11 November 2026

- Recorded ADR-002 Cloud-first Deployment: DigitalOcean VM with Docker Compose,
  Supabase PostgreSQL durable operational state and MacBook development/admin only.
  The accepted hosting baseline is distinct from remaining runtime proposals.

### Completed on 11 November 2026

- Added the cloud deployment architecture and updated v0.3 specification/roadmap
  with persistent scheduling, missed-job recovery, restart/health policies,
  backups, independent monitoring and recovery/readiness acceptance criteria.
- Proposed recovery/retention targets for review; no runtime milestone completed.
- Continued existing PR #1 and preserved ADR-0002 governance, prior documents and
  implementation history. No provisioning, deployment, merge or vault changes.

### Accepted on 10 November 2026

- Recorded the owner's decision that GitHub is canonical for all project code,
  tests, infrastructure and documentation, with Space for collaborative drafting.
- Recorded markos-design as the target home for code and docs, with PR review
  superseding direct-to-main changes; existing implementation migration is pending.

### Completed on 10 November 2026

- Imported the complete v0.3 review draft without approving runtime milestones.
- Added the v0.3 roadmap, architecture reconciliation, migration gates and explicit
  separation of earlier M0–M16 from v0.3 M0–M8.
- Added documented code, test, configuration and infrastructure boundaries and a
  PR template while preserving the flat document layout and prior design records.
- Updated README, project memory and the earlier specification's governance note.
  Vision and prior evaluation artifacts remain unchanged; no deployment or vault
  changes were made.

### Accepted

- Added the M10 Automated Read-Only Synchronization milestone proposal.
- Accepted M10 for implementation.
- Recorded the implemented M0–M9 software milestone history.
- Accepted M11 Controlled PARA Filing.
- Accepted M12 Raw Document Ingestion and Grounded Source Notes.
- Accepted M13 Filesystem-Native Research and Markdown Concept Memory.
- Established Markdown as the durable semantic-memory layer without embeddings or vector search.
- Accepted M14 Knowledge Maintenance and Health.
- Accepted M15 Native Mac App.
- Accepted M16 Background Operations and Distribution.
- Established the read-only PARA migration-audit strategy and initial folder findings.
- Recorded weekly knowledge review and periodic index, link and provenance health checks.

### Completed

- Recorded M10 implementation in `markos` commit `c60400d`.

### Evaluated

- Evaluated Graphify 0.9.40 against a read-only temporary copy of the initial
  concept folders and the existing Localization Research semantic graph.
- Conditionally selected Graphify as a replaceable semantic-discovery provider
  while retaining MarkOS for exact Obsidian links, provenance and safe writes.
- Ran a local Ollama pilot and rejected the tested Qwen 2.5 7B variants for
  production semantic extraction after malformed output and fabricated source
  paths; recorded the required qualification and validation gates.
- Added the MarkOS graph-validation contract and recorded that `qwen3:14b`
  produced a structurally consistent control graph but failed the strict gate
  because Graphify emitted zero source-location coverage.
- Added deterministic heading-aware source mapping: the Qwen 3 control now
  passes with complete mapped provenance, while the genuine-note run remains
  rejected because Graphify omitted mandatory relationship confidence scores.
- Added constrained Graphify confidence normalization; reprocessing now lets
  both existing Qwen 3 artifacts pass structurally while preserving explicit
  normalization provenance and rejecting missing non-extracted scores.
- Measured narrow-chunk reproducibility: short ordinal chunk paths fixed model
  path corruption and reduced citation breadth, but repeated Qwen 3 graphs had
  low node/edge overlap and only 75-percent per-run chunk coverage.
- Qualified Claude Sonnet through Graphify's Claude Code backend: both runs
  cited all prepared chunks with complete mapped provenance, but both violated
  the confidence contract and node/edge overlap remained too low to scale.

---

## Design Milestone D0 – Foundation

### Added

- Created the `markos-design` repository.
- Established the Obsidian design vault.
- Created the initial folder structure.
- Created the GitHub repository.
- Established the documentation workflow.
- Added `Vision.md`.

### Decisions

- The design repository is the authoritative source for architectural decisions.
- Markdown is the canonical representation of all design documentation.
- Documents are reviewed before being committed.
- Accepted documents are considered stable and are only changed when new information or architectural decisions require it.

### Next

- ADR-0000 — Why MarkOS Exists.
