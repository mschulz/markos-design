# New runtime infrastructure boundary

The accepted topology is DigitalOcean / Docker Compose with Supabase PostgreSQL and RabbitMQ as the internal event bus. Originals use cloud object storage (provider pending); no personal Markdown mount, PARA tree or DuckDB service is required.

The independent runtime will add reviewed migrations, broker topology/policies/permissions, an isolated synthetic Compose harness and backup/recovery/monitoring definitions through bounded PRs. Do not package or expose the archived local application as the cloud runtime.

See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[ADR-003 RabbitMQ Internal Event Bus]], [[MarkOS Cloud Deployment Architecture]] and [[MarkOS v0.3 M0 Implementation Plan]]. Exact operating profile, version/digest, provider cost and recovery evidence remain gates. This correction provisions and deploys nothing.
