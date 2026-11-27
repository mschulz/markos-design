# M7 production profile plan

Status: approved on 2026-11-27, with the rule changes below. Nothing is provisioned. Owner decisions are under "Decisions".

The M7 production profile runs MarkOS on one DigitalOcean VM with Supabase PostgreSQL. It is the profile that the M2-E gate (items 8 and 9 in `docs/m2-gmail.md`) waits for. It covers sign-in, object storage, secrets, the HTTPS entry point and the Compose services. Backup drills, metrics and the replacement-VM runbook stay in later M7 work.

## Starting point

- `docs/decisions.md` accepts one DigitalOcean VM running Docker Compose, with Supabase PostgreSQL. The VM must keep working while the MacBook is off.
- Every service today accepts only the `control-process-fixture-v1` profile. `lifecycle/service.py: isolated_dsn` refuses any database that is not local.
- Sign-in uses a synthetic fixture issuer with ES256 tokens.
- Bill originals go to `BillObjects`, a private local folder.
- Secrets are read from environment variables named by `*_secret_ref` settings. The mail worker's private key and the Google client secret already use this path (M2-E3).
- `infrastructure/compose.m1-control-test.yml` runs the preview. It is not a deployment manifest.

## Decisions

Owner decisions, 2026-11-27:

1. **Host:** one DigitalOcean VM with Docker Compose. Heroku was considered and rejected.
2. **Sign-in:** Google sign-in (OpenID Connect).
3. **Object storage:** DigitalOcean Spaces.
4. **Secrets:** root-only files on the VM, given to containers as Compose secrets. An encrypted recovery copy is kept in the owner's password manager.
5. **HTTPS:** Caddy on the VM, with automatic Let's Encrypt certificates.

## Design

1. **Profile.** A new profile, `control-process-production-v1`, with its own schema. It turns off every fixture: the fixture issuer, fixture Google, fixture tools and the seed file. A service refuses to start if the profile and its settings disagree. Mail, real Gmail and tools stay off until the owner turns each on.
2. **Database.** The profile names the Supabase host in config. The DSN uses `sslmode=verify-full` with a pinned CA file. The fixture database guard stays for the fixture profile. Bootstrap creates the NOLOGIN groups and one login per service as today. Migrations run only through the migration-role API.
3. **Sign-in.** Owner decision, 2026-11-27: the Google step happens in the MarkOS issuer, not the gateway. The issuer is the separate process that today signs in fixture accounts. In production it signs the person in with Google using the authorization code flow with PKCE, scopes `openid` and `email`. It checks Google's ID token: RS256, Google's issuer, the client ID as audience, expiry and nonce. A configured list maps a Google subject ID (`sub`) to a MarkOS subject. An email address alone never grants access. The issuer then opens its own session and mints its short ES256 tokens, as it does today. The gateway, the session tables, chat dispatch and the per-request session check do not change.
4. **Issuer state.** The fixture issuer keeps its sessions in a private file. Owner decision, 2026-11-27: the production issuer keeps its sessions and signing key in PostgreSQL under its own login, so backups and restore cover them. A restart keeps sessions. Revoking an issuer session blocks the next request, as today.
5. **CLI sign-in.** The CLI keeps its PKCE onboarding through the issuer. The person signs in to Google in the browser that the CLI opens.
6. **Object storage.** A Spaces adapter with the same interface as `BillObjects`: content-addressed keys, scoped by owner and domain, SHA-256 checked on every read. The bucket is private. The access key can read and write that bucket only.
7. **Secrets.** Each secret is a 0600 file owned by root in `/etc/markos/secrets` on the VM. Compose mounts each file only into the service that needs it. Services read a file path instead of an environment variable. Nothing secret is in the image, the repository, the logs or the Compose file.
8. **HTTPS.** Caddy is the only public listener, on ports 80 and 443. It forwards to the gateway on the private Compose network. The VM firewall allows 80 and 443 from anywhere and SSH from the owner only. PostgreSQL is not on the VM. RabbitMQ and its management port are private.
9. **Services.** One Compose file, `infrastructure/compose.production.yml`: Caddy, RabbitMQ, gateway, issuer, publisher, consumer, workers, scheduler and the mail worker. Each long-running service has `restart: unless-stopped`, a health check, resource limits and graceful shutdown. Images are pinned by digest. Docker starts at VM boot.

## Rule changes

Approved by the owner on 2026-11-27. Because the Google step is in the issuer, the identity rules in `docs/decisions.md` stay as they are: the gateway still verifies ES256 issuer tokens and rechecks the issuer session on every request. That section records the additions below.

1. **Google tokens.** Only the issuer checks Google's RS256 ID token. The algorithm, issuer and audience come from config, never from the token.
2. **Accepted risk.** If the Google account is compromised, an attacker can sign in until the owner removes the subject mapping or the membership. Mitigation: the Google account has 2-Step Verification.
3. **Personal mode.** The M2-E decisions put the OAuth app in Personal mode, where only the creating account can authorize it. If sign-in uses the same Google Cloud project, only the owner can sign in. Other people with the approver or viewer role would need a second project. One project, owner-only sign-in, until a second person needs access.

## Delivery

Each step is reviewed and merged separately. Tests never call Google, Supabase or DigitalOcean.

1. **M7-A plan:** this document.
2. **M7-B Google sign-in in the issuer:** the issuer's Google sign-in, the ID token check, the subject mapping and issuer state in PostgreSQL. Tests use the fixture Google, extended to issue ID tokens signed with a local test key.
3. **M7-C Spaces adapter:** the S3-compatible bill object adapter. Tests run against a local S3-compatible container in CI.
4. **M7-D production profile and Compose:** the profile schema, file-based secrets, the Supabase database settings, Caddy and `compose.production.yml`. CI boots the file with local stand-ins for Supabase, Spaces and Google, and checks that every fixture path is off.
5. **M7-E provisioning runbook:** docs only. The steps the owner follows to create the VM, firewall, domain, Supabase project, Spaces bucket and secret files. Nothing is provisioned without the owner's go-ahead.

Bill and mail-record deletion, object erasure and backup expiry (from the retention decision) follow M7-E as separate PRs.

## Open questions

1. **Domain name** for `<host>`. Caddy and the Google redirect URIs need it.
2. **Region.** Proposed: DigitalOcean `syd1` and Supabase `ap-southeast-2` (Sydney), so the VM, database and bucket are close together.
3. **Sizes and plans.** The VM size, the Supabase plan and the Spaces plan. The Supabase plan must not pause on inactivity and must include backups. The 4 vCPU / 8 GiB VM in `docs/deployment.md` is an unmeasured proposal.
4. **S3 client library.** A small SigV4 client on `httpx2`, or `boto3`. Proposed: `httpx2`, so MarkOS keeps one HTTP library.

Checked at setup, not yet verified: that the Supabase `postgres` role can create the NOLOGIN groups and service logins, and whether Spaces supports object versioning or locks.
