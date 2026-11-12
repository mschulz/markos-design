# New runtime infrastructure boundary

The accepted topology is DigitalOcean / Docker Compose with Supabase PostgreSQL and RabbitMQ as the internal event bus. Originals use cloud object storage (provider pending); no personal Markdown mount, PARA tree or DuckDB service is required.

M0-B adds the first ordered PostgreSQL migration and an explicit migration-role library API, exercised only against a dedicated synthetic local instance. See [[MarkOS v0.3 M0-B Durable Storage]]. No cloud SQL is applied. Broker topology/policies, a broader synthetic harness and backup/recovery/monitoring definitions remain later bounded PRs. Do not package or expose the archived local application as the cloud runtime.

See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[ADR-003 RabbitMQ Internal Event Bus]], [[MarkOS Cloud Deployment Architecture]] and [[MarkOS v0.3 M0 Implementation Plan]]. Exact operating profile, version/digest, provider cost and recovery evidence remain gates. This correction provisions and deploys nothing.
