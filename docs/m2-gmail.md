# M2 Gmail ingestion plan

Status: approved on 2026-11-26. M2-A and M2-B are implemented in `src/markos_runtime/mail/` and migrations 0019 and 0020. M2-B2 is implemented in `src/markos_runtime/api/mail_alerts.py`. M2-C1 is implemented in migration 0021, `mail/optin.py` and `mail/sealing.py`. M2-C2 is implemented in `src/markos_runtime/api/mail_connect.py` and the web Mail page. M2-D is implemented in migration 0022 and `mail/bills.py`. M2-E is in progress: E1 records the gate checklist below. E2 adds the real Google clients in `mail/google.py`. E3 runs them in the synthetic fixture process against a fixture Google. Owner decisions are under "Decisions".

M2 brings email into MarkOS. It reads changes from one opted-in Gmail mailbox, stores each new message once, and keeps a processing status for each one. The "message ingested" event moves to M3. It never sends, deletes or changes mail. Deciding what an email is about stays in M3. The one exception is a narrow, owner-chosen label rule for bills (M2-D).

Roadmap acceptance: duplicate and out-of-order mailbox notifications create one durable source record and event, and MarkOS recovers from cursor gaps.

## Starting point

- `docs/specification.md` section 6 sets the design: least-privilege OAuth, a Gmail watch to Cloud Pub/Sub, incremental reads by history ID, and full resync when history is lost.
- `docs/decisions.md` sets the rules M2 must keep:
  - PostgreSQL is the authority. RabbitMQ distributes events and is never the archive. Pub/Sub is separate ingress, not the internal bus.
  - Schedules and missed-run recovery are database-backed. Production Gmail ingestion runs in the cloud, not on the MacBook.
  - Provider tokens are encrypted and never reach the browser. Logs never hold tokens, mail bodies or raw errors.
  - Real accounts and Gmail stay off until separately reviewed.
- K1 already stores bill originals (`BillObjects`), checks them on every read, and imports Amber and Origin PDFs.

## Design

1. **Mailbox source.** One opted-in mailbox per owner and domain. MarkOS stores the mailbox state: the label allowlist, the history cursor, the watch expiry, and whether the mailbox is connected. The Gmail address is stored only as a keyed hash.
2. **Message records.** Each message is stored once per mailbox, keyed by Gmail message ID. A record holds the thread ID, history ID, received time, label IDs, attachment references and a processing status. Mail bodies are not stored. Attachments are fetched only for allowlisted labels (M2-D).
3. **Incremental reads.** A worker asks Gmail for changes since the stored history ID and saves new messages and the new cursor in one transaction. Duplicates and out-of-order changes have no effect, because messages are keyed by ID and the cursor only moves forward.
4. **Cursor gaps.** If Gmail no longer holds the history for the cursor, MarkOS runs a bounded resync. It lists the allowlisted labels only, back to a fixed start date, and records a "resync" notice.
5. **Change alerts.** A Gmail watch sends alerts to a Pub/Sub topic. A Pub/Sub **push** subscription sends each alert to `POST /api/v1/mail/alerts` over HTTPS, so MarkOS makes no Google requests while the mailbox is idle. An alert only says "something changed". The endpoint accepts a request only with a Google-signed OIDC token whose issuer, audience and service account match the configured values. It records the alert, which makes the poll due now, and returns at once. It never fetches mail and never reads the alert's email address. A request without a valid token gets 401. A malformed alert gets 204 and is counted as dropped, so Pub/Sub does not retry it. If the database is down, the endpoint returns 503 and Pub/Sub retries. The mail worker checks the database for due work every few seconds, then reads changes by history ID and fetches messages and attachments through the Gmail API. A database schedule renews the watch daily. Gmail requires renewal at least every 7 days.
6. **Backstop poll.** A database schedule also reads changes every 15 minutes, so a lost alert delays mail but never loses it.
7. **Events.** Moved to M3 on 2026-11-26, owner decision. Every event needs an intended consumer, and nothing consumes mail until M3 classification. M2 stores each message with status `ingested`. M3-D adds the event, its consumer and bindings, and announces stored messages that are still `ingested`. PostgreSQL stays the record, so no mail is lost meanwhile.
8. **Opt-in and tokens.** An administrator of the owner and domain connects the mailbox from the web app through Google OAuth with PKCE. The gateway seals the refresh token with the mail worker's public key, so only the worker can open it. Disconnecting stops new work at once. The worker then stops the watch, revokes the token at Google and deletes it. If Google is out of reach, the worker retries, and the token is deleted after 1 day anyway. The stored records stay. Every connect, label change and disconnect is audited with its labels. Resyncs are recorded as in M2-A.
9. **Scope.** `gmail.readonly` is the narrowest scope that can read attachments. Gmail has no per-label scope, so the label allowlist is enforced in MarkOS code and tested.

## Delivery

Each step is reviewed and merged separately. M2-A to M2-D use only a fake Gmail and synthetic mail.

1. **M2-A mailbox records and fake Gmail:** migration for the mailbox and message tables, a Gmail client interface with an in-memory fake, incremental reads and cursor-gap resync. A `can_ingest_mail` grant gates the mail functions. Until M2-C1, the synthetic worker opened the mailbox itself with an address hash and label list. Migration 0021 removed that path.
2. **M2-B schedules:** daily watch renewal and the 15-minute backstop poll, as due times on the mailbox with leases. Recording an alert makes the poll due now. The worker claims due work every few seconds. Owner decision on 2026-11-26: the schedules are due times on the mailbox row, claimed under a lease using database time. The generic scheduler only emits events, which would bring the M3-D event work forward.
3. **M2-B2 push endpoint:** the alert route, the OIDC token check against Google's published keys, a config gate that is off by default, and a database login with only the `can_ingest_mail` grant. Tests sign tokens with a local test key and never call Google.
4. **M2-C owner opt-in**, in two PRs:
   - **M2-C1 storage and rules:** sealed token storage, connect, label and disconnect functions for administrators, audit, and the worker's disconnect pass. Tests use a placeholder token and the fake Gmail.
   - **M2-C2 OAuth flow:** the browser connect and disconnect pages and the OAuth exchange with PKCE. Tests use a fixture OAuth provider.
     The routes and the Mail page exist only when the gateway gets an explicit consent adapter and the worker's public key. The real Google consent client, and where its secrets and keys are stored, are part of the M2-E gate.
5. **M2-D bills from a label:** PDF attachments in one owner-chosen label go through the existing K1 PDF import. Size, type and page limits apply. Duplicates are skipped by SHA-256. Bills from an unknown supplier are counted and left for review.
6. **M2-E real-account gate:** a checklist the owner approves before MarkOS connects to the real mailbox. It covers the Google Cloud project, OAuth client, Pub/Sub topic and its publisher grant, the push subscription with its service account and audience, the public endpoint URL and certificate, the acknowledgement deadline, a dead-letter topic, secret storage, the host it runs on, and the retention decision. No code runs against real Gmail before this gate. M2-E is three PRs:
   - **M2-E1 checklist:** docs only. It records the owner's decisions, the facts checked against Google's documentation, and the gate checklist.
   - **M2-E2 Google clients:** the real consent client and the real Gmail API client, using `httpx2`. Tests run them against an in-process fake Google built on the fake Gmail, and never call Google.
   - **M2-E3 fixture wiring:** the synthetic fixture process runs the real clients against a fixture Google, `control_lifecycle/google_fixture.py`. It serves Google's OAuth, Gmail and push-key paths under one loopback origin. The gateway gets the Mail routes and the alert route, and a new mail process runs the worker pass. Mail is off unless `mail_enabled` is set on the gateway and the mail process. The process test connects in the browser, sends a signed push, checks the sync and disconnects.
     The fixture profile still fixes `real_accounts_enabled` to false. Loading the real OAuth client, the token key and the push settings for a real mailbox waits for the production profile in M7.

## Tests

- Duplicate and out-of-order alerts and history entries create one message record and one event.
- A lost history cursor triggers a bounded resync that finds every allowlisted message once.
- A lost alert is caught by the backstop poll.
- The alert endpoint refuses missing, unsigned, expired, wrong-audience and wrong-account tokens, and oversized bodies. It records nothing for them.
- The watch is renewed before expiry, also after a worker restart or a missed schedule.
- Messages outside the allowlist are not fetched or stored.
- Tokens never appear in logs, responses, events or the browser. Disconnect deletes the token.
- Owner and domain isolation, CSRF and roles apply, as on other routes.
- M2-D: a bill attachment imports once, and the stored bill passes the K1 read-time recheck.

## Decisions

The owner approved these on 2026-11-26.

1. **Where real ingestion first runs:** M2-A to M2-D stay synthetic. Real use waits for M2-E and a decision on the cloud host.
2. **Alert path:** a Pub/Sub push subscription to a public HTTPS endpoint on the cloud host, plus a 15-minute backstop poll. This replaced a pull subscription on 2026-11-26, owner decision, because push makes no Google requests while the mailbox is idle. Push cannot run on the MacBook without a tunnel, which `docs/deployment.md` rules out.
3. **Scope:** `gmail.readonly`. MarkOS code enforces the label allowlist.
4. **Bills from a label:** M2-D is in M2. One owner-chosen label feeds the K1 PDF import. M3 classification replaces it later.
5. **Backfill depth:** the first import and every resync read back to 1 January 2020.
6. **Retention:** message records and bill originals stay until the owner deletes them. Disconnecting deletes the token only. `docs/decisions.md` still lists real-data retention as open: deletion, privacy and backup erasure need their own review before M2-E.
7. **M2-C decisions,** owner, 2026-11-27:
   - Only the `administrator` role connects or disconnects the mailbox and sets its labels. Any member can read its status.
   - The token is sealed for the mail worker: X25519, HKDF-SHA256 and AES-256-GCM, bound to the owner and domain. The gateway holds only the public key, so it cannot open a stored token.
   - Disconnect: the worker stops the watch and revokes the token, then deletes it. After 1 day the token is deleted even if Google stays out of reach.
   - M2-C is split into M2-C1 and M2-C2.
8. **M2-D decisions,** owner, 2026-11-27:
   - An administrator picks one bill label on the Mail page. It must be on the allowlist, and the change is audited. With no bill label, no bills are imported. A new allowlist without the bill label clears it.
   - A mail bill names no person. It records `mail` as its source and its Gmail message ID. Its audit row uses the all-zero actor and the operation `bill-import.mail`.
   - Bills are imported from every stored message in the bill label, back to 1 January 2020.
   - The worker writes originals to the same private bill folder as the gateway, so the bill page rechecks them the same way. The worker and the gateway must run on the same host.
   - Only attachments with the type `application/pdf` are read. Each gets one outcome: imported, duplicate, unsupported supplier, over the limit, or rejected with a reason. Unsupported and over-limit bills are counted for review and stay in Gmail.
   - The bill folder holds at most 100 originals. The database allows 500 bills. So 100 is the limit in practice.

9. **M2-E decisions,** owner, 2026-11-27:
   - The OAuth app uses Google's **Personal mode**. It needs no verification, its refresh tokens do not expire after 7 days, and Google shows no unverified-app screen. Only the Google account that creates the project can authorize the app, and the mode cannot be changed later.
   - M2-E is three PRs, as listed under "Delivery". The Google clients use `httpx2`, pinned in the `control` extra. Owner decision on 2026-11-27: `httpx2` replaced `httpx` because `mcp` already brings it, so MarkOS keeps one HTTP library.

## Checked against Google's documentation

Checked on 2026-11-27 through search summaries of Google's pages, not by reading each page in full. The owner confirms Personal mode on Google's own page before creating the project.

- **OAuth app mode.** `gmail.readonly` is a restricted scope. An External app in "Testing" gets refresh tokens that expire 7 days after consent. Personal mode avoids this (see decision 9). Each Google account can hold at most 100 refresh tokens for one OAuth client. A new one past that limit silently invalidates the oldest.
- **Gmail push.** Gmail publishes as `gmail-api-push@system.gserviceaccount.com`, which needs the Pub/Sub Publisher role on the topic. Gmail sends at most one alert per second per mailbox and drops the rest. MarkOS reads changes by history ID, so a dropped alert loses nothing.
- **Push authentication.** The endpoint must check the token's signature, `aud`, `exp`, `email` and `email_verified`. `api/mail_alerts.py` already checks all of them. If the subscription sets no audience, Google uses the endpoint URL.
- **Acknowledgement and retention.** The acknowledgement deadline defaults to 10 seconds and can be 10 to 600 seconds. For push it is also the request timeout. Pub/Sub keeps unacknowledged alerts for 7 days by default, from 10 minutes to 31 days. A dead-letter policy allows 5 to 100 delivery attempts.
- **Subscription expiry.** A subscription expires after 31 days without activity by default. An idle mailbox may send no alerts for that long, so the gate sets the subscription to never expire.

## M2-E gate checklist

The owner approves every item before MarkOS connects to the real mailbox. `<host>` is the public HTTPS origin of the gateway.

1. **Host.** The cloud host is chosen (an M7 decision). The gateway and the mail worker run on the same host. `<host>` has a valid certificate.
2. **Retention.** The real-data retention review in `docs/decisions.md` is closed: deletion, privacy and backup erasure.
3. **Project.** A new Google Cloud project is created while signed in as the mailbox's own Google account. The Google Auth Platform is in Personal mode. Only the Gmail API and Pub/Sub are enabled.
4. **OAuth client.** A web application client with one redirect URI, `<host>/api/v1/mail/callback`. The only Gmail scope is `gmail.readonly`.
5. **Topic.** One Pub/Sub topic. `gmail-api-push@system.gserviceaccount.com` has the Pub/Sub Publisher role on that topic only.
6. **Push subscription.**
   - Endpoint `<host>/api/v1/mail/alerts`, with OIDC authentication.
   - A dedicated service account with no other roles. Its email and the audience match the gateway's push settings.
   - Acknowledgement deadline: the default 10 seconds. The endpoint only records the alert.
   - Message retention: the default 7 days. Expiry: never.
7. **Dead-letter topic.** A dead-letter topic with 5 delivery attempts. The grants the Pub/Sub service agent needs for it are checked on Google's dead-letter page at setup.
8. **Secrets.** The OAuth client secret and the mail worker's private key are stored as the M7 production profile sets out. M2-E3 reads them from secret references, as the fixture process does. Neither is in the repository, the logs or the browser.
9. **Code.** M2-E2, M2-E3 and the M7 production profile are merged. Mail is still off until the owner turns it on.
10. **First connect.** The owner connects from the Mail page, picks the label allowlist and checks the first sync and the status counts. Two details of `mail/google.py` are checked against real Gmail only at this point:
    - The partial-response selector for message parts. It reads five levels of MIME parts and asks for no body data or file names. A wrong selector makes Gmail answer HTTP 400.
    - The watch filter value `INCLUDE`. Google's guide writes it in capitals and the reference page in lower case.

## Out of scope

- Classifying email, which is M3.
- Sending, deleting, labelling or archiving mail.
- Storing mail bodies.
- More than one mailbox.
- Agent access through `/mcp`.
- Production hosting, backups and monitoring, which are M7 gates.
