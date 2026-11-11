# MarkOS test boundary

Each approved implementation milestone adds automated tests alongside its code
and documentation. Use synthetic mail, bills and source documents, with provider
adapters replaced by test doubles. Do not read a real vault or use live accounts.

The first v0.3 foundation tests must cover schema rejection, domain/owner isolation,
duplicate delivery and idempotent operations, broker outage and recovery, worker
restart, durable outbox/inbox state and replay with side effects suppressed.
Later milestones add the acceptance evidence in [[MarkOS v0.3 Roadmap]].

There is no executable test suite in this repository yet. Choose the toolchain
after inspecting the separate implementation; do not report a runtime milestone
complete from this scaffold.
