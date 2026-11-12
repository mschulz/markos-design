# Configuration authority

Existing `legacy.example.toml` belongs to the archived local Markdown/DuckDB application. Its `vault_path` spelling, and PR #7's proposed `markdown_root` rename, are legacy compatibility details. Neither configures the new runtime or implies PARA storage.

Proposed new runtime contracts/examples will live under `config/runtime/`, with versioned event/subscription/identity schemas, secret references and explicit runtime-only settings. The independent preflight must never discover legacy config, note folders or the root package. Prior PR #6 examples require fit review before adaptation.

No required `markdown_root` appears in the event foundation. A future Markdown export adapter may explicitly configure an ordinary output folder without PARA or Obsidian conventions. Originals and structured state remain in cloud source storage/PostgreSQL.

Never commit credentials, live DSNs, personal source data or secrets. See [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] and [[MarkOS v0.3 M0 Implementation Plan]].
