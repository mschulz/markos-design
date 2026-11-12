# MarkOS source boundary

M0.0 imports the existing v0.2 modules here without changing their bytes. They
provide the read-only Markdown/DuckDB application, CLI, local API and experiments.
See [[MarkOS v0.3 M0.0 Import Record]] for provenance and validation.

Approved future runtime code will live in `markos.runtime` as described in
[[MarkOS v0.3 M0 Implementation Plan]]. No cloud runtime or new dependencies are
introduced by this import; preserve legacy behavior and settings boundaries.
