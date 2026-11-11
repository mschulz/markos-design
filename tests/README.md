# MarkOS test boundary

Each approved implementation milestone adds automated tests alongside its code
and documentation. Use synthetic mail, bills and source documents, with provider
adapters replaced by test doubles. Do not read a real vault or use live accounts.

The first v0.3 foundation tests must cover schema rejection, domain/owner isolation,
duplicate delivery and idempotent operations, broker outage and recovery, worker
restart, durable outbox/inbox state and replay with side effects suppressed.
RabbitMQ-specific M0 scenarios are defined in [[ADR-003 RabbitMQ Internal Event Bus]]:
confirm/return handling, lost-confirm retry, independent subscriber queues,
competing workers, domain/owner rejection, crash after durable commit before ack,
bounded poison-message review and loss of previously confirmed broker data.
Verify replay from retained events/dispatch state with original IDs, side effects
suppressed and expired approvals rejected.

The proposed single-VM profile is RabbitMQ 4.4.0 / aio-pika 10.2.3 / durable classic
queues. Before launch, run the ADR-003 workload and pressure scenarios against the
pinned image/dependency lock: both queue bounds with reject-publish (including
partial fan-out), message oversize, bounded in-flight publishes, prefetch,
connection/channel quotas, memory/disk blocking and backlog recovery. Test
30-day replay and 90-day deduplication boundaries, holds for unfinished work,
payload deletion and backup-RPO limits with a controlled clock. These are future
acceptance requirements, not tests run by this documentation PR.

Later milestones add the acceptance evidence in [[MarkOS v0.3 Roadmap]].

There is no executable test suite in this repository yet. Choose the toolchain
after inspecting the separate implementation; do not report a runtime milestone
complete from this scaffold.
