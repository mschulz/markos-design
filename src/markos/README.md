# MarkOS source boundary

Approved service, worker, adapter and agent code will live here. This directory
currently contains no executable application code.

Before adding code, inventory the existing separate `markos` implementation,
choose a history-preserving import and review the v0.3 architecture conflicts
in [[MarkOS v0.3 Roadmap]]. The Python baseline is provisional; package names,
dependency management and entry points must be settled in the migration PR.
Do not overwrite or duplicate existing services without that review.
