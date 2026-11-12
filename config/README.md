# MarkOS configuration boundary

Store versioned configuration schemas, defaults and sanitized examples here
when the applicable milestone is approved. Agent manifests, topic permissions,
model budgets and notification rules need validation and audit.

Use references to credentials managed outside GitHub. Never commit tokens,
passwords, personal addresses or live account configuration. Runtime settings
and secrets remain outside this source tree; no configuration is activated by
this documentation increment.

M0.1 includes credential-free runtime.example.toml, a synthetic no-op event and
subscription manifest, and generated schemas. The preflight loads only explicit
runtime config; it ignores legacy Markdown root settings and opens no connections.
See [[MarkOS v0.3 M0.1 Contracts and Configuration]] for trust boundaries.
