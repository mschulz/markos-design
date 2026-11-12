# New runtime infrastructure boundary

The accepted topology is DigitalOcean / Docker Compose with Supabase PostgreSQL and RabbitMQ as the internal event bus. Originals use cloud object storage (provider pending); no personal Markdown mount, PARA tree or DuckDB service is required.

M0-B adds the first ordered PostgreSQL migration and an explicit migration-role library API, exercised only against a dedicated synthetic local instance. See [[MarkOS v0.3 M0-B Durable Storage]]. No cloud SQL is applied. M0-C adds credential-free broker definitions, a version/digest profile and additive transport-state migration 0002, exercised on disposable CI services. Runtime identities have no configure permissions. A broader lifecycle harness and production backup/recovery/monitoring remain later work. See [[MarkOS v0.3 M0-C RabbitMQ Transport]]. Do not package or expose the archived local application as the cloud runtime.

See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[ADR-003 RabbitMQ Internal Event Bus]], [[MarkOS Cloud Deployment Architecture]] and [[MarkOS v0.3 M0 Implementation Plan]]. Exact operating profile, version/digest, provider cost and recovery evidence remain gates. This correction provisions and deploys nothing.

M0-D adds migration 0003 for task attempts/results/review, global lease slots and versioned schedule/occurrence/scan/control history. It preserves 0001/0002 checksums and requires explicit migration-role application. No cloud SQL or running service is configured. See [[MarkOS v0.3 M0-D Execution and Scheduling]].

M0-E adds migration 0004 for recovery capabilities, policy gates, replay permits/history, source observations/tombstones and explicit staged cleanup. `compose.m0-replay-test.yml` is a disposable CI volume-loss acceptance harness only. Its unique project labels guard broker-volume deletion; PostgreSQL survives the test and the owned project is removed afterward. It is not a production deployment or the M0-F lifecycle. See [[MarkOS v0.3 M0-E Replay and Retention]].
