# Changelog

## 2026-11-28 — M7-F mail record deletion

- Migration `0026_mail_record_deletion.sql`: deleting a mail record removes its labels, attachment references and bill outcomes, and keeps a tombstone with only the Gmail message ID. A resync never stores that message again. The deletion is audited under a hash, not the message ID.
- A bill imported from the message is kept, with its own SHA-256 and Gmail pointer.
- `GET /api/v1/mail/records` lists the newest 100 records. `POST /api/v1/mail/records/{message_id}/delete` deletes one. Both exist only when Gmail opt-in is configured.
- Web app: a Mail records list on the Mail page, with a Delete button and a confirmation dialog. CLI: `mail-records`, and `mail-record-delete --message-id <id>` (preview unless `--confirm`).

## 2026-11-27 — K1-D bill deletion and original erasure

- Migration `0025_bill_deletion.sql`: deleting a bill removes its record and corrections in one transaction and keeps only a tombstone (SHA-256, bill ID, file suffix, Gmail message pointer, erasure time). The deletion is audited.
- `POST /api/v1/bills/{id}/delete` deletes a bill, then erases its original from local storage or Spaces. A missing object counts as erased. If erasure fails, the response is `503 bill-erasure-pending`, the bill stays deleted, and repeating the request retries the erasure. `GET /api/v1/bills` lists erasures still pending.
- A pending tombstone refuses a manual re-import of the same original. After erasure, a manual re-import is allowed. A bill imported from Gmail is never imported again after deletion.
- The mail worker checks the tombstone before it stores a PDF, and erases an original it stored if a deletion happened during the import.
- Web app: a Delete button with a confirmation dialog, and a list of pending erasures. CLI: `bills`, and `bill-delete --bill-id <id>` (preview unless `--confirm`).

## 2026-11-27 — M7-E provisioning runbook and setup script

- `docs/m7-runbook.md`: the owner's steps to provision the Sydney VM (2 vCPU, 4 GiB), firewall, domain, Supabase Pro project and Spaces bucket, then set up and start MarkOS. Nothing is provisioned.
- `markos_runtime.provision`, run by `infrastructure/provision.sh` inside the runtime image, writes the private CA, server certificate, secret files, service configs and broker definitions. It also creates database roles, runs migrations and adds the owner principal. It never overwrites a file, never prints a secret, and reads passwords from hidden prompts.
- The CI production boot test now sets up its stand-ins with the same module, so CI exercises the owner's setup code.
- `infrastructure/site.example.json`: the site file that the script reads.

## 2026-11-27 — M7-D2b production Compose file and boot test

- `infrastructure/compose.production.yml` runs Caddy, RabbitMQ and the 7 MarkOS services, with mail behind `--profile mail`. Images are pinned. Each service has a health check, resource limits, a restart policy, a read-only root and no capabilities. Each secret is a file mounted only into the services that read it.
- `infrastructure/caddy/Caddyfile`: Caddy is the only public listener. It serves HTTPS for `MARKOS_DOMAIN` and verifies the gateway's certificate against the private CA.
- `infrastructure/rabbitmq/production.conf`: TLS-only listeners. Users, permissions and topology load from a definitions secret at boot.
- Fix: under the production profile, the broker client and the topology audit now trust the private CA. Before, they trusted only the system store, so production services could not reach the broker. The fixture paths are unchanged.
- New CI job "Production profile boot" boots the file against a TLS PostgreSQL stand-in for Supabase. It checks that every service is healthy, Caddy is the only listener, sign-in redirects to Google and fixture login returns 404. It does not contact Google or Spaces.

## 2026-11-27 — M7-D2a services run the production profile

- New command `serve-control-production --confirm-production`. It loads only `control-process-production-v1` and reads secrets only from `/run/secrets`. The synthetic commands still refuse the production profile.
- `docs/decisions.md` records the owner's exception to "Everything runs synthetic" for this command. The listed gates still block real data.
- Under the profile, every database DSN must pass the Supabase check. Google uses its real endpoints with system TLS trust. Bill originals go to Spaces, which the gateway and the mail process now require.
- The issuer client reaches the issuer at `https://issuer:<port>/fixture` on the Compose network. The issuer has no seed file, so Google sign-in is the only way in, and the gateway always uses it.
- The worker runs conversations without the fixture tool transport.
- The gateway uses the new `ProductionBillApiConfig` route profile on the public origin, and listens on `gateway_port` (default 18443) behind Caddy.
- The API and the conversation worker take exactly one confirmation: `confirm_synthetic` or `confirm_production`. The production one works only with the production route profile.

## 2026-11-27 — M7-D1 production profile

- `control_lifecycle/production.py` adds `ProductionServiceConfig`, profile `control-process-production-v1`, with its own schema. It has no fixture settings: no seed or state file, no fixture Google, no tool server and no loopback-only endpoints.
- The public origin must be an HTTPS DNS name on the default port. The issuer must be private on the Compose network. Sign-in is always Google: the issuer requires the Google client ID and subject map.
- Mail stays off unless the gateway and the mail process both turn it on. Tools and migrations at start stay off. Spaces settings are for the gateway and the mail process only.
- `FileSecrets` reads each secret from a file in `/run/secrets`, as `secret()` reads the environment. A missing, linked, oversized, multi-line or non-regular file counts as missing.
- `production_dsn` refuses a database DSN unless it names the configured Supabase host, uses `sslmode=verify-full` and pins the configured CA file.
- No service accepts the profile yet. M7-D2 wires it into the services, Caddy and the Compose file.

## 2026-11-27 — CI runs the tests that need the control extra

- Five test files skip without the control extra: PDF reading, the Google fixture, mail sealing, the Gmail clients and the mail bill import. The `contracts` job has no extra, and the `control-api` job ran only `tests/control_api`, so no CI job ran them.
- The `control-api` job now runs every test file that calls `pytest.importorskip`, outside the API, browser and process suites.
- With `MARKOS_REQUIRE_CONTROL_EXTRA=1`, a file skipped for a missing import fails instead.

## 2026-11-27 — M7-C Spaces bill object adapter

- `knowledge/spaces.py` adds `SpacesObjects`, an S3-compatible store for bill originals (DigitalOcean Spaces). It has the same `put` and `read` as `BillObjects`: keys are scoped by owner and domain and named by SHA-256, and every read checks the hash.
- Requests are signed with SigV4 on httpx2; no new dependency. Uploads ask for a private ACL. Redirects are never followed. Reads stop at the size limit.
- A new `BillStore` interface lets the bill routes and the mail worker take either store. Nothing wires Spaces in yet; the production profile (M7-D) does that.
- Spaces has no object-count limit, unlike the 100-file preview folder.
- Tests: the AWS SigV4 example, in-process refusals, and a disposable SeaweedFS S3 in the `control-api` CI job. MinIO's container images could not be pulled, so SeaweedFS replaced it.

## 2026-11-27 — M7-B2 Google sign-in in the issuer

- The issuer can sign a person in with Google: the authorization code flow with PKCE, scopes `openid` and `email`. It alone holds the OAuth client secret.
- The issuer checks Google's ID token: RS256 against Google's published keys, Google's issuer, the client ID as audience, expiry, `azp` and the nonce.
- A configured map from Google subject ID to MarkOS subject decides who may sign in. An email address never grants access.
- After Google answers, the issuer mints its usual one-use MarkOS code, bound to the gateway's PKCE challenge and nonce. Tokens, sessions and the per-request session check do not change.
- Migration 0024 stores each pending Google sign-in for 10 minutes, used once. The state and nonce are HMAC lookups, and the PKCE verifier is sealed.
- The gateway's `/app/authorize` redirects to Google instead of showing the fixture code form. `/auth/google/callback` checks Google's state against a sealed cookie. A browser then finishes sign-in as before. The CLI shows a one-use code to paste.
- Off by default. The gateway's `google_signin` switch and the issuer's `google_signin_subjects` map turn it on. Only the fixture Google is wired. Production settings follow in M7-D.

## 2026-11-27 — M7-B1 issuer state in PostgreSQL

- Migration 0023 adds the `markos_issuer` schema. The issuer keeps its signing key, one-use codes, sessions and refresh tokens there instead of a private JSON file. A restart, backup or restore now covers them.
- Only the new NOLOGIN group `markos_issuer_client` can call the issuer functions. The issuer connects with its own login. The runtime and gateway logins cannot reach issuer state.
- The issuer seals its signing key with a new secret, `MARKOS_CONTROL_ISSUER_STATE_KEY`, before storing it. Codes and refresh tokens are stored as HMAC lookups only.
- Database time sets every expiry: codes last 60 s, sessions 8 h.
- Fixture accounts still come from the private seed file on each start and are never stored.
- `DurableFixtureIssuer` and the issuer `state.json` are removed. The test bootstraps and the preview bootstrap create the new group and login.
- An existing preview needs the new login and state key before it runs this version. Nothing upgrades it automatically.

## 2026-11-27 — M7-A production profile plan

- Added `docs/m7-production.md`: the plan for the production profile on one DigitalOcean VM with Supabase PostgreSQL.
- Owner decisions: Google sign-in, DigitalOcean Spaces for bill originals, root-only secret files on the VM, and Caddy for HTTPS.
- Owner decision: the Google step happens in the MarkOS issuer, which then mints its own ES256 tokens. The gateway's token and per-request session checks stay unchanged. `docs/decisions.md` records the Google subject mapping and the accepted risk.
- Owner decision: the production issuer keeps its sessions and signing key in PostgreSQL under its own login.

## 2026-11-27 — Real-data retention: Gmail pointers and bill originals

- Owner decision: email stays in Gmail. MarkOS keeps only the mail record as a pointer, never the body or other attachments.
- Owner decision: bill PDFs from the bill label are kept unchanged in object storage, with the SHA-256 and the Gmail pointer.
- Owner decision: deleting a bill or mail record removes its data at once and keeps a tombstone that blocks reimport. Backups: 7 daily and 4 weekly. Logs: 14 days. Audit metadata: 365 days. Deletion and erasure are not built yet.
- Owner decision: a Gmail filter files electricity bill emails under `Financial/Riverhills/Electricity` and skips the Inbox. MarkOS keeps read-only Gmail access and reads that label. Recorded in the M2-E gate checklist.
- Docs only. No code or migration changes.

## 2026-11-27 — M2-C1 mailbox opt-in storage and rules

- Migration 0021. Only an `administrator` of the owner and domain can connect the mailbox, set its label allowlist or disconnect it. Each change writes the audit table and `markos_control.mail_history`, with its labels.
- The Gmail refresh token is stored sealed in `markos_runtime.mail_tokens`. `mail/sealing.py` seals it with the mail worker's X25519 public key, bound to the owner and domain. The gateway cannot open a stored token.
- The worker's claim now carries the sealed token. A new label allowlist makes the watch renewal due at once.
- Disconnect stops new work at once. The worker's next pass stops the watch, revokes the token and deletes it. If Google is out of reach, the pass is retried after the lease. After 1 day the token is deleted anyway.
- The synthetic `open_mailbox` function is removed. A mailbox it opened has no token, so the migration marks it disconnected.
- One address per owner and domain, as before. Reconnect is refused while a disconnect is still running.
- The OAuth browser flow follows in M2-C2.

## 2026-11-26 — M2-B2 Gmail push alert endpoint

- Added `POST /api/v1/mail/alerts` for Pub/Sub push. It is off unless the gateway is built with a `MailAlerts` adapter.
- The route accepts a request only with an RS256 token signed by a Google key, with Google's issuer, the configured audience and the configured service account (`email` and `email_verified`). Otherwise it returns 401 and records nothing.
- A valid alert is recorded with `note_mail_alert`, which makes the poll due now. The route returns 204 at once. It never fetches mail and never reads the alert's email address.
- A malformed alert, or an alert for a disconnected mailbox, gets 204 and is counted as dropped, so Pub/Sub does not retry it. A database failure, or Google's keys being out of reach, gets 503 so Pub/Sub retries.
- `GoogleKeys` fetches Google's published keys through PyJWT, with caching. Tests use a local RSA key and never call Google.
- The gateway service does not build the adapter yet. That waits for the mail database login and the real Gmail client. No migration.

## 2026-11-26 — M2-B watch renewal and backstop poll

- Migration 0020 adds due times to the mailbox: the next poll, the next watch renewal, the watch expiry, an alert counter and a lease with a fence. Database time is the only clock.
- `note_mail_alert` makes the poll due now. Repeated or old alerts change nothing more.
- `claim_mail_work` hands due work to one worker under a lease. A crashed worker's lease expires, and the next claim retries the work.
- `finish_mail_work` records work only under the current fence. After a sync, the next poll is due in 15 minutes, or at once if an alert arrived during the sync. After a renewal, the next one is due in 1 day, or 1 day before the watch expires if that is sooner.
- `markos_runtime.mail.worker` runs one pass. It renews the watch and syncs when due. `alert_history` reads only the history ID from a Gmail alert, never the email address. The M2-B2 push endpoint will use it.
- There is no network Gmail client yet. Tests use fakes. Migrations 0001–0019 are unchanged.

## 2026-11-26 — M2 plan: Pub/Sub push delivery

- Owner decision: Gmail alerts arrive by Pub/Sub push to an authenticated HTTPS endpoint, not by pull. The 15-minute backstop poll stays.
- The plan adds M2-B2, the push endpoint, and the push items in the M2-E checklist.

## 2026-11-26 — M2-A mailbox records and fake Gmail

- Migration 0019 adds the `mailboxes`, `mail_messages` and `mail_resyncs` tables. Runtime clients have no direct access to them.
- A new `can_ingest_mail` service grant gates four functions: `open_mailbox`, `mailbox_state`, `store_mail` and `advance_mail`.
- Each message is stored once, keyed by its Gmail message ID. A record holds the thread ID, history ID, received time, labels, attachment references and status `ingested`. Mail bodies and attachment names are not stored.
- The database refuses a message without an allowlisted label.
- The history cursor only moves forward. A worker that read an old cursor gets "stale" and writes nothing.
- `markos_runtime.mail` adds a read-only Gmail interface and one sync pass. A pass reads changes since the cursor and fetches only messages with an allowlisted label.
- A missing or lost cursor starts a resync of the allowlisted labels back to 1 January 2020. Each resync is recorded.
- There is no network Gmail client yet. Tests use an in-memory fake with invented mail.
- The "message ingested" event moves to M3, by owner decision. Migrations 0001–0018 are unchanged.

## 2026-11-26 — Tool outages no longer disable the grant

- Fixed an intermittent failure of "Synthetic control preview and restore". After a restart, the tool worker's first check of the tool server could run past its 5-second limit. The worker treated that as a protocol mismatch, disabled the tool grant, and the approved call was revoked.
- The transport now raises `ToolUnavailable` when the server gives no response: connection refused or reset, a timeout, or an expired deadline. The worker then defers the approved call and keeps the grant. TLS failures, non-200 answers and schema mismatches still disable the grant.
- The worker loads the MCP SDK at startup, not inside the first check's time limit. The control image now precompiles bytecode.
- Added tests for both outcomes. No migration.

## 2026-11-26 — K1-C3 bill arithmetic rules and acceptance examples

- Added "Bill arithmetic rules" to `docs/k1-bill-knowledge.md`: GST, totals, earlier balances, export credits and credit bills, estimated reads, and tariff structures. Each rule names the tests that enforce it.
- Added `tests/test_acceptance.py`: six invented bills with expected facts and answers. They cover an ordinary Amber bill, an ordinary Origin bill, an unpaid earlier balance, a credit bill with a negative total, an estimated read and a split-rate quarter.
- Added `scripts/bill_coverage.py`: counts fact states over a private folder of PDF bills. It prints counts only, never file names or bill values.
- No code or migration changes.

## 2026-11-26 — K1-C2 bill history

- Added `GET /api/v1/bills/history`: every bill in the scope, Amber, Origin and pasted text together, in billing-period order. Each row shows the supplier, days, bill total, kWh, export credit, cost per day and kWh per day.
- Totals include GST for every profile. Days count both the first and last day of a period.
- Gaps and overlaps between billing periods appear as notices. Bills without a clear period are listed last.
- A figure that is not found or corrected shows as "not established", never as a number.
- The history checks each original's SHA-256 but does not parse every PDF again. A bill whose original is missing or changed is listed without figures. The bill page still rechecks its report in full.
- Added `POST /api/v1/bills/history/questions` for "How much electricity did I use each period?" and "How has my bill changed?". Answers name the bills they use.
- The bills page adds a history table and the two questions.
- Migration 0018 adds the read-only `bill_history` function. Migrations 0001–0017 are unchanged.

## 2026-11-26 — Origin balance sign fix

- The Origin profile kept no sign on any amount. A credit brought forward or a credit bill would fail the totals check and show as unreconciled. Now only exports and credits are stored as positive credits, and every other amount keeps its sign.
- A credit brought forward adds the notice "The amount due is reduced by a credit from an earlier bill."
- Corrections accept a negative bill total, for credit bills.
- Reports for bills without credit balances are unchanged, so stored bills still pass the read-time recheck.

## 2026-11-26 — K1-C1 bill corrections

- Added `POST /api/v1/bills/{id}/corrections`. Operators correct any fact with a reason of up to 200 characters, or withdraw a correction with a null value. Viewers cannot correct.
- Corrections are append-only in `bill_corrections`. The extracted report never changes, and every read still rechecks it against the original.
- Bill responses add `corrections` and `effective`: the latest correction of each fact on top of the report, with the bill's citations kept.
- After a money correction, the Amber or Origin totals are checked again. If they do not add up, money figures stay unreconciled.
- Answers use effective values and say when a figure was corrected.
- Migration 0017 adds corrections and raises the bill limit from 100 to 500 per scope. Migrations 0001–0016 are unchanged.
- The bills page shows the extracted value and correction history next to each corrected figure, with correct and withdraw controls.

## 2026-11-26 — K1-B Origin Energy PDF profile

- Added `knowledge/origin.py`, the Origin Energy profile (`origin-pdf-v1`, 13 facts). It reads tables that pypdf splits into one cell per line.
- A quarter with a price change has two charge and two export tables. The profile adds both, and checks each table against its total.
- The amount due must equal the bill total plus the balance brought forward. Any mismatch, unknown row or estimated read marks money facts unreconciled.
- `POST /api/v1/bills/pdf` picks the profile from the bill's text. Reads recheck with the profile named in the stored report.
- Migration 0016 lets `bill_records` hold `origin-pdf-v1` reports. Migrations 0001–0015 are unchanged.

## 2026-11-26 — K1-B Amber Electric PDF upload

- Added `POST /api/v1/bills/pdf`: raw `application/pdf` bodies up to 2 MiB and 20 pages. Other routes keep the 64 KiB limit.
- Added `knowledge/pdf.py`: pypdf runs in a child process with time, CPU and memory limits, and returns page text or a bounded error.
- Non-Amber, scanned, encrypted and malformed PDFs are rejected and not stored.
- PDF originals are stored as `.pdf` objects. Every read parses the original again and checks the stored facts.
- Migration 0015 lets `bill_records` hold `amber-pdf-v1` reports. Migrations 0001–0014 are unchanged.
- The bills page has a PDF upload control and shows citations by page and line.
- Added pypdf 7.1.1 to the `control` extra.

## 2026-11-26 — K1-B Amber Electric extractor

- Added `knowledge/amber.py`: reads Amber Electric bill page text into 15 cited facts with page and line.
- Every charge and credit total is checked. A mismatch or unknown summary line marks money facts unreconciled.
- Evidence gains an optional `page`. Answers cite it, and cover the due date and the new charges.
- No PDF parsing, route or storage yet; that is the next PR.

## 2026-11-26 — K1-B plan

- Added the K1-B plan for Amber Electric PDF bills to `docs/k1-bill-knowledge.md`.

## 2026-11-26 — Replace stale milestone docstrings

- Rewrote 10 docstrings that named old milestones (M1-A to M1-E) or "future" work as present-tense descriptions. Five schema descriptions changed to match.

## 2026-11-26 — Route table for signed-in API routes

- The 10 signed-in API routes are now named handlers in one `(method, path)` table.
- Sign-in, CSRF, query and scope checks run in the same order as before, so error codes do not change.

## 2026-11-26 — Remove duplicated product code

- Shared one chat claim decoder, loopback HTTPS URL check, strict JSON parser and exact int/bool validators.
- Shared the secret and database checks between the two lifecycle services. `work_seconds` and `shutdown_seconds` are now used.
- Shared one sealed paging cursor across page lists, activity and governance.
- Old-format activity cursors now return 400 instead of 409.
- Removed the write-only API counters and `VerifiedIdentity.nonce`. API errors map through one table.

## 2026-11-26 — Remove dead code found in the code review

- Removed `RuntimeConfig`, `load_config`, the `preflight` command, its example and schema. `preflight-control` stays.
- Replaced `ProposedLimits` with `TransportLimits`, which keeps only the 12 limits the transport reads.
- Moved the test-only `ControlStore` session and staging methods to `tests/control_store_support.py`.
- Removed `require_live`, `result_hash`, `Membership.supports`, `lifecycle/monitor.py` and two unused API routes.
- Removed the closeout ledgers and `rabbitmq/profile.json`. The production gates now live in `docs/decisions.md`.

## 2026-11-26 — Safety and bug fixes from the code review

- Database tests refuse ports 5432, 15432 and 25432 and host redirection. CI uses 55432.
- Missing credentials and wrong database or broker targets now stop a service instead of retrying forever.
- Removed the daily non-dry-run cleanup branch, which the decisions forbid.
- Oversize review records hash the committed bytes. Bill periods accept extra spaces. CLI `approvals` works without governance.
- The dependency test checks the base install only, so it passes with extras installed.
- The preview CI job prints task and approval state counts on failure.

## 2026-11-26 — Move the runtime to the repo root and the docs into docs/

- `services/runtime/` is now the repo root: `src/`, `tests/`, `pyproject.toml`, `uv.lock`, Dockerfiles.
- Root docs moved to `docs/` with short kebab-case names. Wikilinks became relative links.
- CI, Compose build contexts and test path lookups updated. No runtime code changed.

## 2026-11-26 — Prune the abandoned v0.2 code and the milestone documents

- Delete the archived v0.2 application (`src/markos/`, root `tests/`, root `pyproject.toml` and `uv.lock`), its documents, the import manifest, the legacy config example and the document template. Git history keeps all of them.
- Fold the six ADRs and sixteen M0/M1 milestone records into `docs/decisions.md`, keeping only rules that still govern the code, each with its reason. Move the preview run steps to `docs/local-preview.md`.
- Replace the document inventory and metadata gate with a broken-link check, and delete `Documentation Authority Index.md`.
- Rewrite `README.md`, `CLAUDE.md`, `AGENTS.md` and the `config/`, `infrastructure/` and `services/runtime/` READMEs as short current guides. No runtime code, migration or CI job changes.

## 2026-11-26 — Lean electricity-bill Knowledge Base first slice

- Bring a useful K1 bill-to-answer workflow ahead of Gmail and broad classification, preserving the event-driven architecture and later milestone identifiers.
- Add an opt-in Electricity bills screen with pasted/sample text import, immutable private original objects, scoped PostgreSQL figures/provenance, deduplication and source-line answers. Missing/conflicting figures and unsupported questions remain explicit.
- Add migration 0014 and extraction/object/API/browser acceptance. Preserve 0001–0013, the archived application and the running local preview. PDF/OCR, real providers/bills, model use, merging and deployment remain separate work.

## 2026-11-25 — Preserve cancellation reasons during concurrent service failures

- Fix PR #28’s service-check handler so it cannot overwrite another request’s saved cancellation reason while waiting for the execution fence.
- Add migration 0013, preserving migrations 0001–0012 and the existing fail-closed authority checks. Select locked, eligible request IDs instead of a time window.
- Add a regression test that observes the competing lock wait and verifies both requests retain the correct explanation.

## 2026-11-13 — Explain cancelled approval requests

- Show Request cancelled with a plain reason and a clear retry action. Record cancellation facts at the transition rather than guessing from later session/settings changes.
- Add optional typed cancellation reasons to the existing scoped approval readers through additive migration 0012. Preserve authentication, immutable intent, decision/dispatch behavior and migrations 0001–0011.
- Recover old signed-out reasons only where retained timestamps prove them; other unrecorded causes get an honest fallback. No request is reactivated.


## 2026-11-13 — Plain preview labels

- Applied first MVP feedback: Sign-in code, Test label, Review test request and Test service access replace visible fixture/credential/grant jargon. Login help and demo notices use plain language.
- Updated existing browser acceptance locators and the preview walkthrough. Request fields, authentication, exact approvals, stored messages and runtime behavior stay unchanged.

## 2026-11-13 — Local synthetic preview setup

- Reviewed main after PR #25; preserved the exact reviewed merge tree.
- Added an optional loopback PostgreSQL host-port override so the preview can coexist with an existing local database. Internal ports, owned-service checks, scopes and defaults remain intact; the hosted preview job exercises the alternate port.
- Replaced a fixed 0.3-second MCP fixture startup sleep with checks of both private readiness endpoints after initial start and restart; protocol/deadline checks remain intact.
- This is a local synthetic setup correction, not a cloud deployment or production-readiness claim.


## 2026-11-13 — M1-G lean synthetic preview (review PR)

- Reviewed PR #24 merged main `c590cb2`; both post-merge workflows passed. Preserved its exact tree/history and the archive.
- Prioritized startup/MVP feedback: optional independent image/process preview, scoped persisted synthetic issuer/MCP fixtures and owned local/CI setup; no real integrations or deployment.
- Bounded final HTTP sends and actual blocking-reader socket lifetime; additive migration 0011 shares short-request admission across replicas.
- Added essential packaged browser/CLI/restart/uncertain-effect/dependency/fresh-restore CI and a runbook plus explicit M1/MCP evidence/gap ledger. Full operational closeout remains open; automated maintenance/alerts, broad fault/resource and cloud gates are deferred.


All notable changes to the MarkOS Design repository will be documented in this file.

The format is inspired by *Keep a Changelog*, but organised around design milestones rather than software releases.

---

## Unreleased

### M1-F governance and synthetic tools prepared for review — 13 November 2026

- Reviewed main `231a018` after PR #23; exact M1-E tree/history preserved and all six post-merge checks pass with 770 cases.
- Added an explicit fixed governance profile, reviewed scoped grants, immutable exact approval previews, nonhierarchical approver decisions and durable slot-free waiting with fresh execution mapping.
- Added additive migration 0010, shared capacity/rate/one-time reservations, append-only metadata/reference activity, fenced dispatch/results, durable unknown outcomes and explicit human ledger reconciliation; original no-tool conversation/broker contracts and migrations 0001–0009 remain intact.
- Locked the optional official MCP 2.3.0 / 2026-07-28 profile with strict local HTTPS JSON/request-SSE interoperability and no SDK automatic negotiation/retry/OAuth drivers. Added scoped web/CLI controls, 40 schemas/examples, privileged bounded seven/365-day cleanup/tombstones and governance/limit/retention/protocol/broker/browser fault acceptance.
- Real accounts/OAuth/effects and source/transfer access remain disabled. M1-G still owns packaged process/restore/maintenance/operational closeout. No merge, deployment or personal data access.

### M1-E web and CLI prepared for review — 13 November 2026

- Reviewed merged PR #22/main `4cb4d7b`; exact tree/history preserved and all six post-merge checks pass with 706 tests. Fixed a same-scope activity cursor-purpose consistency defect and added regression coverage.
- Added an explicit synthetic web profile, five accessible responsive pages, exact typed previews, private CLI PKCE session storage and shared authenticated HTTPS commands; approval/tool actions remain disabled.
- Added bounded durable SSE with actor/scope/resource/epoch cursors and shared expiring 1/4/20 admission leases in additive migration 0009. Preserved migrations 0001–0008, base/control dependency pins, archived work and PostgreSQL/RabbitMQ authority.
- Added required disposable Chromium browser acceptance, client/stream security/fault tests, schema/example and [[MarkOS v0.3 M1-E Web and CLI]]. M1-F/G, real identities, packaged process/restore/production gates remain separate. No merge or deployment.

### M1-D durable test agent conversation prepared for review — 13 November 2026

- Reviewed merged main `8311f01` after PR #21; exact reviewed tree/history preserved, six post-merge checks green with 623 tests. No blocking issue in M1-C's synthetic API scope.
- Added a separately constructed conversation profile, strict conversation/admission/status contracts, atomic message/request-event/consumer/outbox admission, version/current-authority checks, opaque polling cursors and durable scoped cancellation.
- Added migration 0008 and a fixed checksum acknowledgement worker sharing the existing two execution slots, with transactional exactly-one reply/result/activity/completion outbox, current fixture session checks and epoch/fence/lease rejection. Existing no-op schedules and archived code remain intact.
- Extended the closed wire/parser and exact RabbitMQ route audit, preserving committed bytes through publication/handoff. Added administrator-only topology examples, real HTTPS/RabbitMQ/competing-worker CI, rollback/redelivery/response-loss/authority/limit/upgrade tests and [[MarkOS v0.3 M1-D Durable Test Agent Conversation]].
- Real providers, interactive clients/SSE, governance/tools, scheduled cleanup, packaged conversation processes/full restore and production gates remain open. No merge, deployment or personal data access.


### M1-C authenticated shared API prepared for review — 12 November 2026

- Reviewed merged main at `6a87fb0` after PR #20; exact reviewed tree/history preserved and all five post-merge checks pass (533 runtime cases plus optional compatibility and docs). No blocking issue within M1-B persistence scope.
- Added explicit optional fixture-only ASGI API with signed ES256/PKCE verification, current upstream/local authority, encrypted provider credentials, opaque browser/CLI sessions, CSRF/Origin/Host protections, scoped registry/configuration/status/activity and private schema-aware health.
- Added migration 0007, shared write/auth/key/flow bounds, an inert separate loopback-HTTPS profile/schema, additive `invalid` error, packaged migration fingerprints and required optional-profile CI with actual localhost HTTPS tests. Existing migrations, root archive, base pins, active M0 events/topology and no-op execution are preserved.
- Added [[MarkOS v0.3 M1-C Authenticated Shared API]] and updated current documentation/authority inventory. Real IdP integration, interactive clients/SSE, chat promotion, governance/tools, scheduled cleanup and API process/restore/production acceptance remain later gates. No merge or deployment.


### M1-B control-plane persistence prepared for review — 12 November 2026

- Reviewed merged main at `ea076ff` after PRs #18/#19; tree/history preserved and all five post-merge checks green (471 runtime cases plus optional compatibility and documentation). No blocking finding within M1-A contract scope.
- Added migration 0006 and explicit ControlStore with separate gateway role/mapping, private base tables, current session/membership/generation checks, hashed session rotation/refresh/logout, versioned registry/configuration histories and scoped conversations/messages/activity.
- Added atomic, idempotent held message proposals with current agent/config revisions and database-checked content hashes; no active event/outbox/broker/task promotion until M1-D. Privileged bootstrap remains explicit and separate from runtime startup.
- Added real PostgreSQL isolation/concurrency/rollback/session/limit/upgrade evidence and [[MarkOS v0.3 M1-B Control Plane Persistence]]. Human authentication/API, chat execution, full approval/audit/retention and real provider/production gates remain later work. Migrations 0001–0005, dependencies, schemas and archive are unchanged. No merge or deployment.


### M1-A contracts and identity prepared for review — 12 November 2026

- Reviewed PR #18 at `082f5d7`; no blocking consistency findings and documentation CI green. Preserved its unmerged planning commit as this review branch’s prerequisite.
- Added strict human/membership, registry/config, chat/reference, activity, fixture-grant/intent/approval/audit and error descriptors, 18 new schemas/examples and connection-free control preflight. Frozen test configuration cannot enable gateway/chat/tools/real accounts.
- Added [[ADR-006 Control Plane Identity and Trust Boundary]] and [[MarkOS v0.3 M1-A Contracts and Identity]]; separated structural validation from future authentication, DB authority, atomic dispatch and distributed enforcement.
- Diagnosed post-daemon-restart CI readiness probes exceeding their two-second process budget under the disposable 0.25-CPU cap; allow five seconds for that test probe while preserving readiness/business assertions and application deadlines.
- Locked a separate optional FastAPI/Uvicorn/PyJWT/Jinja profile and added an in-memory compatibility CI probe. Base dependencies, active M0 events/topology/SQL/execution, archive and source history remain intact. No merge, deployment, account connection or provider effects.


### Merged-main review and M1 control-plane plan — 12 November 2026

- Reviewed main at `ec52a27` after owner-authorized merges of PRs #15–#17; no blocking finding within synthetic M0 scope. Post-merge CI passed 323 runtime cases and documentation checks. Verified original heads/history, exact reviewed tree and archive/migration preservation.
- Added [[MarkOS v0.3 M1 Control Plane Implementation Plan]] with repository path changes, proposed trusted human/API/database boundary, versioned registry/configuration, typed durable chat, shared web/CLI, synthetic approvals/MCP and seven ordered PR slices. M1-01–M1-22 specify acceptance and faults; they are future requirements, not passing M1 tests.
- Updated current instructions, README, specification, roadmap, M0 status and authority inventory to distinguish merged M0 evidence from proposed M1 work. Real provider/approval/backup/VM/monitoring gates remain open. Documentation only; no implementation, account connection, merge or deployment.


### M0-F synthetic lifecycle and closeout prepared for review — 12 November 2026

- Reviewed PR #16 at `83c230f`: no blocking correctness/architecture findings within synthetic replay/retention scope; 294-case runtime CI and documentation CI green. Preserved PR #15 documentation reconciliation and both prerequisite histories.
- Added explicit synthetic role processes, private health/heartbeats, bounded graceful shutdown/reconnect, redacted telemetry, restricted independent image/Compose and required lifecycle/restore CI.
- Added migration 0005 generation fencing and private restore rotation; earlier migration checksums, root source/tests/lock and snapshots remain unchanged. Old generations cannot finalize restored claims even after numeric fence collision.
- Added [[MarkOS v0.3 M0-F Lifecycle and Closeout]], [[MarkOS v0.3 M0 Runbook]] and a machine-readable acceptance ledger. Production readiness remains false; real source/backup erasure, VM boot/capacity, independent monitoring and real approvals/MCP remain gates. No merge, deployment or personal data access.


### PR #15 documentation reconciliation — 12 November 2026

- Reconciled AGENTS, authority-index, roadmap and README with main at `4d06339`, preserving the merged MCP safeguards, rules-first routing and ADR-002 correction alongside M0-D's synthetic execution/scheduling boundary.
- Incorporated main into the review branch while preserving all original PR #15 commits. Runtime source/tests, locks and migrations are unchanged; M0-E remains a separate PR.
- Retained the documentation workflow/checker and all 35 inventoried Markdown documents. The pull request remains unmerged; no deployment or real-provider access.



### M0-E replay and retention prepared for review — 12 November 2026

- Reviewed PR #15 at `8286870`: no blocking correctness/architecture findings; 256-case CI green. Preserved current main's governed MCP/rules-first routing decisions and the original prerequisite branch.
- Added bounded retained-event recovery (including confirmed broker work), one-use scoped older-replay permits, suppressed effects, synthetic source access/checksum observations, deletion tombstones and staged 30/90-day cleanup with holds and dry-run-first invocation; additive migration 0004 preserves earlier checksums.
- Added an actual disposable broker-volume-loss drill preserving PostgreSQL, queued-deletion rejection, age/permit/cleanup boundary and shared-operation/fairness tests. Required acceptance spans 294 runtime cases plus separate documentation checks. No merge, real provider/erasure, personal data or deployment.


### M0-D synthetic execution and scheduling — 2026-11-12

- Reviewed PR #12 at `22fbf25`: no blocking correctness/architecture findings; 194-case CI green.
- Added explicit fixed no-op execution with PostgreSQL leases/fences/global two-slot limit, attempts, retry and durable review; hard process recovery and commit-failure tests.
- Added strict versioned interval/daily schedules, DST/missed policies, atomic occurrences/events/outbox/cursor and immutable scan/control history; additive migration 0003 preserves prior checksums.
- Required suite: 256 tests, including 75 PostgreSQL and 12 broker/database cases. Real approvals, agents/tools, replay/retention and service lifecycle remain gates. No merge or deployment.

### M0-C RabbitMQ transport prepared for review — 12 November 2026

- Reviewed PR #11’s transaction/identity/migration boundaries and passing CI. Added pinned, independent RabbitMQ transport, live topology/validated identity checks, actual confirms/returns, durable retry/review state, manual ack after commit and independent watchdog/generation recovery.
- Added additive migration 0002, operational review schema, credential-free broker definitions/profile and required real-broker/database CI. The suite has 194 cases; local RabbitMQ absence skips 11 explicitly. See [[MarkOS v0.3 M0-C RabbitMQ Transport]]. No agent worker, full replay, merge, cloud provisioning or deployment.

### M0-B durable storage prepared for review — 12 November 2026

- Reviewed PRs #9/#10 for the independent architecture; corrected PR #10’s invalid CI runner context, and its corrected hosted workflow passes.
- Added explicit PostgreSQL storage, ordered checksummed migration, login-bound roles/RLS, atomic event/outbox and inbox/task writes, dedup/conflict quarantine and fenced claims. 145 independent tests include 34 real PostgreSQL cases. See [[MarkOS v0.3 M0-B Durable Storage]]. No RabbitMQ adapter, worker execution, merge, cloud SQL or deployment.

### M0-A implementation prepared for review — 12 November 2026

- Prepared independent M0-A contracts/configuration for review in services/runtime, with a fresh minimal lock, validation-only CLI, synthetic schemas/examples and scoped CI. 104 independent tests and clean-wheel isolation checks pass. Source/manifest trust and unimplemented database/broker guarantees are explicit; see [[MarkOS v0.3 M0-A Contracts and Configuration]]. Archived source, root package/lock and personal data remain unchanged. No merge or deployment.

### Agent MCP access proposed on 12 November 2026

- Added ADR-005 and specification/roadmap gates for governed agent MCP tools: explicit grants, owner/domain isolation, scoped disclosure/effect approvals, durable audit and unknown-outcome reconciliation. RabbitMQ remains the internal bus.
- Added future MCP-01–MCP-10 acceptance cases and phased synthetic-first delivery from M1; existing M0 scope is unchanged. SDK/server selection, measured limits and audit retention remain review decisions.
- Updated navigation, contributor guidance and the documentation inventory. No runtime feature, dependency, credential, server, merge or deployment is enabled.
- Kept proposed ADR-005 profile: official `mcp==2.3.0`, HTTPS/protocol 2026-07-28, bounded shared-VM resources/attempts/payloads/deadlines and 365/7/14-day audit/copy/log retention. Documented SDK hidden-retry/OAuth/streaming controls, holds and deletion-safe restore requirements.
- Reconciled latest main, preserving merged M0-A–M0-C and M3 routing. Specified task-fenced gateway dispatch outside broker callbacks/transactions, nested execution/tool capacity, durable approval waits and unchanged native classifier independence; no runtime code or dependency changes.
- Clarified that MCP-01–MCP-10 precede real MCP enablement; native adapters are independent and M7 repeats operational drills. Added a read-only documentation CI workflow, dependency-free inventory/metadata/local-link checker and synthetic failure tests. The workflow makes no runtime acceptance claim and changes no branch protection.

### Completed on 12 November 2026

- Corrected ADR-002’s active decision/durability text to RabbitMQ and the settled PostgreSQL/source-object/derived-Markdown authority. Removed the stale open Markdown-authority conflict; hosting placement is unchanged. Updated against merged M0-A–M0-C without altering their code or evidence. Documentation only; no PR merge or deployment.


- Corrected the architecture baseline after the owner's explicit abandonment of the original design. ADR-004 supersedes PARA/Markdown-canonical/DuckDB/desktop assumptions and legacy-code reuse; RabbitMQ is selected internally and PostgreSQL/source objects own durable state.
- Updated Vision, specification, roadmap, M0 plan, active contributor/boundary docs and authority labels. Proposed an independent runtime project and acceptance matrix; marked old designs/evaluations/imports as historical. Exact v0.2 snapshots, source code, dependency lock and import manifest remain unchanged.
- PR #7's naming cleanup and PR #6's contracts are prior work, not the replacement foundation. No runtime, source migration, merge or deployment is performed by this documentation correction.

### M3 rules-first routing specified for review — 12 November 2026

- Updated [[MarkOS v0.3 Specification]] and [[MarkOS v0.3 Roadmap]] to apply reviewed email-routing rules before an interchangeable Jev or LLM classifier. Application code retains owner/domain permissions, agent selection, thresholds and approval control; uncertain/no-match/conflicting cases retain durable review state.
- Added M3-A–M3-D gates for shared labelled fixtures, adapter contracts, held-out comparison against rules alone, measured accuracy/coverage/cost/latency, and atomic audited dispatch/corrections. No winning provider or numerical threshold is assumed; privacy, budgets and fallback policy require review.
- Reviewed the M0-A–M0-D branches for integration: isolate the M3 amendment revision/insertion points to preserve M0 versions and review-status text. Explicitly require versioned email contracts/consumer intent, task fencing and provider calls outside broker handoff/database transactions. Freeze thresholds before held-out acceptance; retuning requires fresh acceptance data.
- Documentation only: no classifier calls, runtime changes, merge, deployment or personal-data access. M0 implementation remains separate.

Earlier entries below describe work at the time; they do not reinstate superseded plans.


- Prepared M0.0 as a two-parent history-preserving import from markos d5d46cb,
  stacked on PR #3. Source application/tests and uv.lock remain byte-identical;
  source README/AGENTS are preserved as historical documents, active machine
  paths removed, and canonical instructions/ignore rules reconciled.
- Verified the original 143-test suite and the imported 145-test suite plus
  lock, Ruff and strict mypy gates. Added import/config safety guards and a
  source/destination hash manifest; see the M0.0 Import Record. Review/merge
  remain pending; no real-vault access, runtime implementation or deployment.

- Reviewed PR #3 and inspected markos main d5d46cb: Python 3.13/uv with a
  read-only knowledge stack and tests, but no event/task/PostgreSQL runtime.
- Added the proposed v0.3 M0 implementation plan, history-preserving import,
  concrete source/schema changes, M0.0–M0.7 slices and A01–A20 acceptance matrix.
- Closed two design gaps: required owner_id in event/inbox/business scope, and
  application handoff watchdog/channel-close recovery for classic consumers.
  Updated ADR-003 (1.2), specification (0.3.4), roadmap (1.4), cloud architecture
  (1.3), navigation and future test guidance. No runtime/import/deployment or
  test execution occurred; this records documentation review and planning only.

- Prepared a follow-up to merged PR #2 with proposed RabbitMQ 4.4.0, aio-pika 10.2.3 and durable classic
  queues, concrete single-VM resource/flow guardrails, 30-day routine replay and
  90-day deduplication floors with unfinished-work holds and deletion gates.
- Updated ADR-003 (1.1), specification (0.3.3), roadmap (1.3), cloud architecture
  (1.2) and future test requirements. Sizing remains unmeasured and subject to
  approval; no limits/retention applied, dependencies installed or deployment run.

- Proposed ADR-003 RabbitMQ Internal Event Bus and updated v0.3 specification
  (0.3.2), roadmap (1.2) and cloud architecture (1.1) for AMQP routing, durable
  handoff, confirms/acks, bounded retries and broker-storage-loss replay.
- Preserved accepted DigitalOcean/Compose/Supabase placement; MQTT is optional
  for device adapters and Google Cloud Pub/Sub remains Gmail ingress.
- Added future M0 broker acceptance scenarios and navigation/project-memory
  references. Runtime tests and milestones remain unstarted; no provisioning,
  deployment, Space or vault changes, or merge are included.

### Accepted on 11 November 2026

- Recorded ADR-002 Cloud-first Deployment: DigitalOcean VM with Docker Compose,
  Supabase PostgreSQL durable operational state and MacBook development/admin only.
  The accepted hosting baseline is distinct from remaining runtime proposals.

### Completed on 11 November 2026

- Added the cloud deployment architecture and updated v0.3 specification/roadmap
  with persistent scheduling, missed-job recovery, restart/health policies,
  backups, independent monitoring and recovery/readiness acceptance criteria.
- Proposed recovery/retention targets for review; no runtime milestone completed.
- Continued existing PR #1 and preserved ADR-0002 governance, prior documents and
  implementation history. No provisioning, deployment, merge or vault changes.

### Accepted on 10 November 2026

- Recorded the owner's decision that GitHub is canonical for all project code,
  tests, infrastructure and documentation, with Space for collaborative drafting.
- Recorded markos-design as the target home for code and docs, with PR review
  superseding direct-to-main changes; existing implementation migration is pending.

### Completed on 10 November 2026

- Imported the complete v0.3 review draft without approving runtime milestones.
- Added the v0.3 roadmap, architecture reconciliation, migration gates and explicit
  separation of earlier M0–M16 from v0.3 M0–M8.
- Added documented code, test, configuration and infrastructure boundaries and a
  PR template while preserving the flat document layout and prior design records.
- Updated README, project memory and the earlier specification's governance note.
  Vision and prior evaluation artifacts remain unchanged; no deployment or vault
  changes were made.

### Accepted

- Added the M10 Automated Read-Only Synchronization milestone proposal.
- Accepted M10 for implementation.
- Recorded the implemented M0–M9 software milestone history.
- Accepted M11 Controlled PARA Filing.
- Accepted M12 Raw Document Ingestion and Grounded Source Notes.
- Accepted M13 Filesystem-Native Research and Markdown Concept Memory.
- Established Markdown as the durable semantic-memory layer without embeddings or vector search.
- Accepted M14 Knowledge Maintenance and Health.
- Accepted M15 Native Mac App.
- Accepted M16 Background Operations and Distribution.
- Established the read-only PARA migration-audit strategy and initial folder findings.
- Recorded weekly knowledge review and periodic index, link and provenance health checks.

### Completed

- Recorded M10 implementation in `markos` commit `c60400d`.

### Evaluated

- Evaluated Graphify 0.9.40 against a read-only temporary copy of the initial
  concept folders and the existing Localization Research semantic graph.
- Conditionally selected Graphify as a replaceable semantic-discovery provider
  while retaining MarkOS for exact Obsidian links, provenance and safe writes.
- Ran a local Ollama pilot and rejected the tested Qwen 2.5 7B variants for
  production semantic extraction after malformed output and fabricated source
  paths; recorded the required qualification and validation gates.
- Added the MarkOS graph-validation contract and recorded that `qwen3:14b`
  produced a structurally consistent control graph but failed the strict gate
  because Graphify emitted zero source-location coverage.
- Added deterministic heading-aware source mapping: the Qwen 3 control now
  passes with complete mapped provenance, while the genuine-note run remains
  rejected because Graphify omitted mandatory relationship confidence scores.
- Added constrained Graphify confidence normalization; reprocessing now lets
  both existing Qwen 3 artifacts pass structurally while preserving explicit
  normalization provenance and rejecting missing non-extracted scores.
- Measured narrow-chunk reproducibility: short ordinal chunk paths fixed model
  path corruption and reduced citation breadth, but repeated Qwen 3 graphs had
  low node/edge overlap and only 75-percent per-run chunk coverage.
- Qualified Claude Sonnet through Graphify's Claude Code backend: both runs
  cited all prepared chunks with complete mapped provenance, but both violated
  the confidence contract and node/edge overlap remained too low to scale.

---

## Design Milestone D0 – Foundation

### Added

- Created the `markos-design` repository.
- Established the Obsidian design vault.
- Created the initial folder structure.
- Created the GitHub repository.
- Established the documentation workflow.
- Added `Vision.md`.

### Decisions

- The design repository is the authoritative source for architectural decisions.
- Markdown is the canonical representation of all design documentation.
- Documents are reviewed before being committed.
- Accepted documents are considered stable and are only changed when new information or architectural decisions require it.

### Next

- ADR-0000 — Why MarkOS Exists.
