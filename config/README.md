# Configuration authority

Existing `legacy.example.toml` belongs to the archived local Markdown/DuckDB application. Its `vault_path` spelling, and PR #7's proposed `markdown_root` rename, are legacy compatibility details. Neither configures the new runtime or implies PARA storage.

M0-A runtime schemas/examples now live under `config/runtime/`: synthetic event/subscription/policy JSON, explicit TOML and four generated schemas. Preflight never discovers legacy config, note folders or the root package. Secret reference names are validated; secret values and connections are not resolved. See [[MarkOS v0.3 M0-A Contracts and Configuration]] for the fit review, trust boundary and limitations.

M0-B’s storage API takes an explicitly supplied DSN; preflight still does not resolve secret values, connect or run migrations. Database identity/scopes come from migration-managed authenticated login mappings, not a caller’s manifest alone. See [[MarkOS v0.3 M0-B Durable Storage]].

No required `markdown_root` appears in the event foundation. A future Markdown export adapter may explicitly configure an ordinary output folder without PARA or Obsidian conventions. Originals and structured state remain in cloud source storage/PostgreSQL.

Never commit credentials, live DSNs, personal source data or secrets. See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] and [[MarkOS v0.3 M0 Implementation Plan]].
