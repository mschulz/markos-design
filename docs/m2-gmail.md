# M2 Gmail ingestion plan

Status: approved on 2026-11-26. M2-A and M2-B are implemented in `src/markos_runtime/mail/` and migrations 0019 and 0020. Owner decisions are under "Decisions".

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
8. **Opt-in and tokens.** The operator connects the mailbox from the web app through Google OAuth with PKCE. The refresh token is encrypted at rest. Disconnecting stops the watch, deletes the token and keeps the stored records. Every connect, disconnect and resync is audited.
9. **Scope.** `gmail.readonly` is the narrowest scope that can read attachments. Gmail has no per-label scope, so the label allowlist is enforced in MarkOS code and tested.

## Delivery

Six PRs, each reviewed and merged separately. M2-A to M2-D use only a fake Gmail and synthetic mail.

1. **M2-A mailbox records and fake Gmail:** migration for the mailbox and message tables, a Gmail client interface with an in-memory fake, incremental reads and cursor-gap resync. A `can_ingest_mail` grant gates the mail functions. Until M2-C, the synthetic worker opens the mailbox itself with an address hash and label list.
2. **M2-B schedules:** daily watch renewal and the 15-minute backstop poll, as due times on the mailbox with leases. Recording an alert makes the poll due now. The worker claims due work every few seconds. Owner decision on 2026-11-26: the schedules are due times on the mailbox row, claimed under a lease using database time. The generic scheduler only emits events, which would bring the M3-D event work forward.
3. **M2-B2 push endpoint:** the alert route, the OIDC token check against Google's published keys, a config gate that is off by default, and a database login with only the `can_ingest_mail` grant. Tests sign tokens with a local test key and never call Google.
4. **M2-C owner opt-in:** the OAuth connect and disconnect flow for operators, encrypted token storage, label allowlist settings and audit. Tests use a fixture OAuth provider.
5. **M2-D bills from a label:** PDF attachments in one owner-chosen label go through the existing K1 PDF import. Size, type and page limits apply. Duplicates are skipped by SHA-256. Bills from an unknown supplier are counted and left for review.
6. **M2-E real-account gate:** a checklist the owner approves before MarkOS connects to the real mailbox. It covers the Google Cloud project, OAuth client, Pub/Sub topic and its publisher grant, the push subscription with its service account and audience, the public endpoint URL and certificate, the acknowledgement deadline, a dead-letter topic, secret storage, the host it runs on, and the retention decision. No code runs against real Gmail before this gate.

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

## Not confirmed

- **OAuth app mode.** For a personal Gmail account the OAuth app stays unverified. I'm not sure how Google treats `gmail.readonly` there. My understanding is that an app in "testing" mode gets refresh tokens that expire after 7 days. That would need a reconnect each week. M2-E must confirm this against Google's current documentation before the owner relies on it.

- **Pub/Sub push details.** My understanding is that the push acknowledgement deadline defaults to 10 seconds, that Pub/Sub keeps undelivered alerts for up to 7 days, and that Gmail sends at most about one alert per second per mailbox. The OIDC claims and the role the push service account needs are also unconfirmed. M2-E must check these against Google's current documentation.

## Out of scope

- Classifying email, which is M3.
- Sending, deleting, labelling or archiving mail.
- Storing mail bodies.
- More than one mailbox.
- Agent access through `/mcp`.
- Production hosting, backups and monitoring, which are M7 gates.
