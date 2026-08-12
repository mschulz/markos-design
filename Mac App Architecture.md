---
title: Mac App Architecture
type: architecture
status: accepted
version: 1.0
owner: Mark Schulz
created: 2026-08-12
updated: 2026-08-12
reviewed: 2026-08-12
tags:
  - architecture
  - macos
  - swiftui
  - user-interface
---

# Mac App Architecture

## Decision

MarkOS will support both CLI and graphical operation. The preferred Mac client
is a native SwiftUI application that controls the existing Python knowledge
engine through a bundled helper and authenticated local protocol.

The CLI and UI will call the same UI-neutral Python application services. They
must not duplicate business rules or invoke and parse each other's user-facing
commands.

## Component Model

```text
SwiftUI Mac app ── authenticated local IPC ── bundled Python helper
                                                     │
MarkOS CLI ───────────────────── shared application services
                                                     │
                                  vault, DuckDB and optional LLM providers
```

The public FastAPI surface remains read-only. Mutating operations use the local
helper protocol and the same proposal, validation, approval, audit and rollback
services used by the CLI.

## User Experience

The app will provide:

- a dashboard for index, watcher, vault and health status;
- an inbox for Markdown and raw documents;
- PARA and concept-folder proposals;
- multi-file Markdown and move-plan diff review;
- concept-link and reciprocal-backlink review;
- raw extraction and grounded source-note previews;
- `knowledge/` notes, contradictions and research suggestions;
- weekly knowledge and health review queues;
- audit history and undo;
- local and external LLM provider settings;
- separate vault-write and external-disclosure controls; and
- menu-bar status and review notifications.

The app remains useful with no LLM configured. Deterministic indexing, search,
link, graph, migration-audit and health capabilities remain available offline.

## Shared Application Services

Before adding UI-specific code, MarkOS will model workflows as typed services:

```text
propose → validate → preview → confirm → apply → audit → synchronize
```

Service inputs and outputs are stable domain models rather than console text or
SwiftUI views. This enables CLI, Mac app and automated tests to exercise exactly
the same safeguards.

## Vault Access

The user selects an Obsidian vault through a native folder picker. A sandboxed
app persists access with a security-scoped bookmark. macOS permission grants the
process access to the selected folder; MarkOS applies a narrower policy:

- reads are allowed for configured discovery and research;
- writes are disabled by default;
- approved writes are restricted to configured inbox and PARA paths;
- source hashes are checked before applying changes;
- collisions and symlink escapes are rejected; and
- external LLM disclosure remains a separate permission.

Credentials are stored in macOS Keychain, never in repository or vault files.

## Scheduling and Background Work

The Python core exposes deterministic non-interactive operations. The Mac app
configures their cadence and displays results.

Recommended defaults are:

| Frequency | Operation |
|---|---|
| Continuous while enabled | Conditional index watcher |
| Daily | Freshness and index-readability check |
| Weekly | Full link, provenance and folder health review |
| Weekly | Knowledge synthesis and research suggestions |
| Monthly | Deep integrity and rebuild verification |

Knowledge synthesis is periodic rather than real time. Weekly batching gives
relationships time to accumulate, reduces LLM noise and cost, and produces one
coherent review queue.

When the app is closed, a later release may register a per-user LaunchAgent
through Apple's Service Management framework. Registration is user-approved,
visible, reversible and never grants permission for silent repairs or content
generation writes.

## Packaging and Distribution

The SwiftUI app bundles the Python helper and compiled dependencies, including
DuckDB. Client, helper protocol and index schema versions are checked explicitly.

Initial development uses local builds. The first public distribution target is
a Developer ID-signed and notarized direct download. Mac App Store distribution
is deferred until sandbox, helper and background-agent constraints are proven.

Full Xcode is required for SwiftUI application development, signing and release.
Apple Command Line Tools alone are insufficient for this stage.

## Alternatives Considered

### PySide6

PySide6 would provide the fastest Python-only prototype and supports packaged
macOS applications. It is a fallback if time-to-prototype becomes more important
than native integration and long-term Mac experience.

### Tauri with Python Sidecar

Tauri can bundle a Python sidecar but adds Rust, Node and web-frontend technology
without a current cross-platform requirement. It is not justified for the first
Mac client.

### Browser UI

A browser UI over FastAPI could validate information architecture quickly, but
it does not provide the desired native permission, menu-bar, Keychain, scheduling
and distribution experience. It may be used only as a disposable prototype.

## Security Boundaries

- The helper listens only through an authenticated local channel.
- Mutating methods are not added to the public read-only API.
- The UI cannot bypass application-service validation.
- Model-generated paths, claims and references are treated as untrusted.
- Scheduled operations generate proposals; they do not approve them.
- Helper crashes and partial operations preserve the original vault and valid index.

## Milestone Mapping

- M14 provides health and periodic knowledge operations consumed by both interfaces.
- M15 builds the SwiftUI client and shared helper protocol.
- M16 adds LaunchAgent scheduling, notifications, signing, notarization and updates.

## Related

- [[Milestones]]
- [[Markdown Memory Architecture]]
- [[PARA Migration Strategy]]
