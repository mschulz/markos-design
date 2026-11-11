# Changelog

All notable changes to the MarkOS Design repository will be documented in this file.

The format is inspired by *Keep a Changelog*, but organised around design milestones rather than software releases.

---

## Unreleased

### Completed on 12 November 2026

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
