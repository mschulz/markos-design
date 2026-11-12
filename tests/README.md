# Validation boundaries

The existing tests protect archived v0.2 behavior and import safety. They do not establish v0.3 acceptance and do not impose PARA, Markdown authority, DuckDB or the old application on the new design. Preserve them with the archive; do not extend the legacy product as the new foundation.

The independent runtime will own its tests under `services/runtime/tests/`, installed without the root project. [[MarkOS v0.3 M0 Implementation Plan]] defines A01–A20: dependency/config isolation, strict event/identity contracts, real PostgreSQL transactions/roles, RabbitMQ confirms/returns, durable handoff, idempotency, watchdogs, scheduling/fencing, replay/deletion and lifecycle/pressure evidence.

Use synthetic sources and isolated broker/database instances; no personal folders, real accounts or provider effects. Legacy regressions and PR #6's pure contract tests are prior evidence, not proof of broker/database durability. Run new gates independently in CI. No new runtime tests are implemented by this documentation correction.
