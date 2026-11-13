---
title: MarkOS v0.3 M1-E Web and CLI
type: implementation-record
status: implemented-for-review
version: 1.0
owner: Mark Schulz
created: 2026-11-13
updated: 2026-11-13
reviewed: 2026-11-13
tags:
  - architecture
  - control-plane
  - clients
---

# MarkOS v0.3 M1-E Web and CLI

## Main review and scope

Reviewed main `4cb4d7bca6a09c3b71950742bccc5a4ce6487c28` after PR #22 merged. Its tree exactly matches reviewed M1-D head `a76a4cecf45b9e43a5bcdfb26ca466f9d7eb0c12`; existing work and history are preserved. All six post-merge checks pass: [runtime/API CI](https://github.com/mschulz/markos-design/actions/runs/43325779904) covers 706 tests and [documentation CI](https://github.com/mschulz/markos-design/actions/runs/43325779922) passes.

Review found one activity-pagination consistency defect: the endpoint accepted a valid encrypted conversation/message cursor belonging to the same actor/workspace because it did not bind cursor purpose. That could skip activity; no cross-scope disclosure was found. This slice fixes exact activity resource/actor/scope/generation binding, adds regression coverage and explicitly requires resynchronization for old activity cursors. Other M1-D limitations remain recorded in [[MarkOS v0.3 M1-D Durable Test Agent Conversation]].

The owner separately authorized M1-E in [[MarkOS v0.3 M1 Control Plane Implementation Plan]]. This slice adds browser and terminal clients of the existing authenticated API, without direct client SQL/RabbitMQ access or a second authority. PostgreSQL remains durable runtime authority, RabbitMQ remains the internal bus, source objects remain originals and Markdown remains derived. GitHub remains canonical for project artifacts. Archived application, PARA, Obsidian, MCP and rules-first M3 boundaries are unchanged. No merge or deployment is performed.

## Explicit test profile and sign-in

`SyntheticWebApiConfig` extends the existing deterministic conversation profile and requires `web_profile=synthetic-web-cli-v1`; `config/runtime/control/web-api.example.json` is a sanitized example. An explicitly constructed `create_app` factory serves `/app` pages only for this profile. Existing M1-C/M1-D factory profiles and connection-free preflight do not gain interactive routes or real accounts. There is no API launcher, automatic migration, cloud provisioning or real Supabase identity adapter in this slice.

Browser sign-in uses the existing one-use state/nonce/PKCE flow, Secure HttpOnly SameSite cookies and same-origin CSRF-checked writes. The fixture authorization form accepts only the injected issuer's opaque synthetic credential, not an arbitrary subject/role. Its short-lived sealed request, separate cookie and CSRF proof bind authorization. A callback redirects to a fixed local page. No bearer appears in rendered HTML, local/session storage or application URLs. CLI authorization opens the same fixture form only when explicitly requested, returns a one-use code to paste into the waiting terminal and exchanges it with the locally held verifier/binding. There are no real provider calls or implicit home-folder credential searches.

The base-install `markos-control` command requires an explicit loopback HTTPS origin, CA file, absolute session-file path and `--confirm-synthetic`. Its explicit session folder must be owner-only 0700, outside every Git checkout, with no symlink file; the atomically replaced credential file is 0600. Owner, mode, ordinary-file type, size, origin and expiry are checked; FIFO, duplicate JSON keys and nonfinite numbers are rejected. The parent is resolved before checking Git ancestry. This short-lived opaque session is needed for API authentication and is never printed. Logout attempts server revocation and removes the local file even if the service cannot confirm revocation. Durable server revocation remains authoritative. OS credential-store integration, real-account login and unattended refresh are later decisions.

## Browser pages and terminal operations

| Page | Implemented behavior |
|---|---|
| Conversations | Scoped cursor-paged history (100 rows/page), plain-text messages, operator creation/send, retained pending-send idempotency key on uncertain responses, durable request status/polling and exact reviewed cancellation for the original current operator |
| Agents and health | Registry/health observations, disabled registration and enable/disable previews with current revision guards; no agent code/model/tool selection |
| Activity | Scoped committed durable activity with resumable authenticated SSE and explicit resync/failure states |
| Approvals | Authenticated typed unavailable/empty placeholder; approval button disabled and no write/dispatch route |
| Configuration | Read-only current revision/details plus administrator-only exact local preview and version-checked apply |

Roles remain independent capabilities, not a hierarchy. The server reauthorizes every operation; hidden buttons, a preview digest or `can_cancel` hint confer no authority. The additive status hint reports only whether the original current operator may request cancellation; the existing cancellation transaction independently checks authority and fences late results. Configuration/registry preview performs no write and freezes the exact displayed request. A stale revision returns 409 and requires reload/new review. Existing browser Origin/CSRF enforcement remains mandatory.

The shell has semantic navigation, labelled controls, visible keyboard focus, live status and a keyboard-operable native review dialog; a 375-pixel layout fits without horizontal overflow. Message/configuration text is inserted as text, never interpreted Markdown/HTML. Restrictive same-origin script/style/connect/form CSP has no inline scripts; all responses remain no-store. Session renewal/logout are explicit. Visible pages recheck authority every ten seconds, clear private content on denial/outage, and stop/clear when hidden before reauthentication on return. Workspace changes abort streams, close previews and fence late responses; pending uncertain sends block workspace/conversation changes. Read retries are bounded; writes never retry automatically. The in-memory pending send does not survive reload, so a user must inspect durable history after losing the tab rather than assume it failed.

CLI commands cover `login`, `logout`, `me`, `status`, `agents`, `agent-register`, `agent-state`, `conversations`, `conversation-create`, `messages`, `send`, `message-status`, `cancel`, `activity`, `watch`, `configuration`, `configuration-preview`, `configuration-apply` and `approvals`. Scoped commands require owner/domain. Typed write documents come from explicit JSON files or stdin, not message/secret arguments. Registry/configuration writes require the digest of the exact preview before applying; a digest authenticates neither actor nor authority. JSON output escapes terminal control characters. Requests validate HTTPS, use an explicit CA, disable proxies/redirects and bound request/response sizes/time. `watch` makes at most three bounded read attempts with the last durable cursor; no write is replayed automatically. Session renewal for CLI remains a fresh explicit login.

For a separately constructed local synthetic fixture server, the command pattern is:

```text
markos-control --origin https://localhost:18443 --ca-file /explicit/test-ca.pem --session-file /explicit/private-client/session.json --confirm-synthetic login
markos-control --origin https://localhost:18443 --ca-file /explicit/test-ca.pem --session-file /explicit/private-client/session.json --confirm-synthetic agents --owner-id OWNER_UUID --domain personal
```

The CA/folder/server must be explicitly prepared in a disposable test environment; these examples neither start a server nor provision an account. `--help` describes per-command JSON inputs. No credential JSON is checked into GitHub.

## Durable streaming and additive migration

`GET /api/v1/activity/stream` uses existing current session/upstream/local membership checks and committed PostgreSQL activity. SSE `Last-Event-ID` takes precedence over the initial query cursor. Encrypted ten-minute cursors bind exact resource `activity-v1`, actor, owner/domain and database epoch. Wrong-purpose/actor/scope cursors return 400; expired, old-generation, legacy-activity or beyond-current-head cursors require explicit 409 `resync-required`. A reconnect resumes strictly after the last delivered sequence; duplicates are suppressed in the browser. No process-local event history, broker subscription or payload/credential-bearing event is introduced.

Migration `0009_web_cli_activity.sql` adds private RLS stream leases and narrow gateway-only admission/renew/close/head functions, plus the derived cancellation hint. It creates no role/account and changes no prior migration bytes. Admission is shared across gateway instances: one stream/session, four/principal and twenty globally. Leases expire after fifteen seconds and cannot renew past ten minutes or after expiry/generation change. Current authority/lease is rechecked at each five-second poll and during long batches; authority work is bounded at ten seconds. Revocation/dependency outage therefore stops further delivery within the tested fifteen-second window. There is no idle database connection/transaction. Disconnect cleanup is cancellation-shielded and bounded at five seconds; a killed process leaves only an expiring lease.

Per frame is at most 8 KiB; one stream sends at most approximately 256 KiB and runs at most ten minutes before a cursor-based reconnect. ASGI sends are limited to two seconds each, with an outer 610-second deadline. Initial request-body reading retains a separate ten-second deadline; streaming does not extend it. Page reads are bounded at 100 rows. Local stream admission is separate from sixteen short-request slots; database admission is authoritative across processes. Exceptions after response start end the connection without sending a second HTTP error or private details. Browser retries at most three times; CLI watch also caps event count/lifetime. Exact proxy buffering/capacity and packaged multi-process operational measurements remain M1-G work.

## Validation and remaining gates

The new browser dependency is a separate development group, `playwright==1.65.1`, with locked greenlet 3.5.6; existing base and optional control pins are unchanged. This follows the [published Python package](https://pypi.org/project/playwright/), [matching browser installation guidance](https://playwright.dev/python/docs/browsers) and [Python library API](https://playwright.dev/python/docs/library). It is not a runtime/browser download dependency of the base wheel. CI installs the pinned disposable Chromium headless shell explicitly and requires browser acceptance in the existing optional API job. There are 32 generated schemas.

Tests cover shared API/browser/CLI reads and writes, preview with no mutation and stale revision conflict, actual HTTPS resumable streaming, wrong-purpose and old-generation cursors, shared 1/4/20 leases, expiry/restore/revocation and dependency outage, private explicit session storage, strict TLS/origin denial, sign-in without bearer output, mobile keyboard navigation, plain-text injection safety, viewer denial, sign-out and authority loss clearing. M1-08/09/13/14/17 have client/stream acceptance evidence within this synthetic scope; broader M1-18 process/proxy/resource enforcement and M1-20 populated process backup/restore remain incomplete. Required broker, volume-loss, lifecycle, wheel, base, optional API and documentation gates are retained. Missing local RabbitMQ is an explicit local skip, never passing broker evidence. Local validation passes 594 base cases, 138 optional API cases, all seven browser cases, nine additional cursor cases and the slow-request deadline regression and the expanded 64-case persistence suite (including populated migration-0008 upgrade preservation). Only broker/dedicated recovery cases are unpassed locally. Lock, Ruff/format, strict mypy in both installations (91 files), 32 schemas, standalone base wheel/CLI/assets, seven documentation-checker tests, 45-document inventory and whitespace checks pass. The required hosted matrix is 770 tests: 608 normal base cases, 153 optional API/transport cases, seven browser cases and two dedicated recovery drills. Hosted results are recorded in the PR.

M1-F still owns synthetic approval/tool governance and M1-G owns packaged API/conversation processes, scheduled secret/lease cleanup policy, OS-level recovery/restore and operational closeout. Real provider adapters, MFA/account recovery, production key custody/credential storage, retention/erasure decisions, cloud namespace/proxy/capacity and monitoring remain unresolved. This is not whole-M1 or production closeout. No personal data, cloud service, merge or deployment is involved.

Related: [[ADR-006 Control Plane Identity and Trust Boundary]], [[MarkOS v0.3 M1-D Durable Test Agent Conversation]], [[MarkOS v0.3 M0 Runbook]] and [[Documentation Authority Index]].
