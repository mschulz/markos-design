# M7 provisioning runbook

Status: not run. Nothing is provisioned. Mark runs each step by hand, in order.
The design is in [m7-production.md](m7-production.md).

This runbook creates real accounts and costs money. It needs no code change.
Mail, Gmail and tools stay off after it.

## Choices

Owner decisions, 2026-11-27:

| Item | Choice |
|---|---|
| Domain | `markos.mcguyver.com` (2026-11-28). It is written `<domain>` below. DNS is at GoDaddy. Leave the `mcguyver.com` records unchanged. |
| Region | DigitalOcean `syd1`. Supabase `ap-southeast-2` (Sydney). |
| VM | 2 vCPU / 4 GiB, Ubuntu 24.04. The Compose limits total about 2.5 GiB. |
| Database | Supabase Pro. It does not pause when idle and has daily backups. |

## Rules for every step

1. **Never paste a secret into a command, a chat, an issue or the repository.** The
   provisioning script reads each secret at a hidden prompt.
2. **Run commands on the VM from the repository root,** as your own user with `sudo`.
3. **Stop at the first failure.** Each provisioning step is safe to rerun. None of
   them overwrites or rotates a secret.

## 1. Google sign-in client

1. In Google Cloud, use the project from M2-E, which is in Personal mode.
   Only the account that created it can sign in.
2. Create an OAuth client of type **Web application**.
3. Add the redirect URI `https://<domain>/auth/google/callback`.
4. Record the client ID. Keep the client secret for step 6.
5. Find your Google subject ID (`sub`). One way: sign in to the
   [OAuth 2.0 Playground](https://developers.google.com/oauthplayground) with scope
   `openid`. Exchange the code, then read `sub` from the ID token. Your email address
   is not used. Only `sub` maps to MarkOS.

## 2. Supabase

1. Create a Pro project in `ap-southeast-2`. Use a long generated database password.
2. Record the direct database host: `db.<project-ref>.supabase.co`.
3. Download the database CA certificate. In the dashboard, open
   **Database settings → SSL configuration**.
4. Turn on SSL enforcement, so the database refuses connections without TLS.
5. Network: the direct host may be IPv6 only. Buy the **IPv4 add-on**, or confirm
   that the VM's containers can reach IPv6. Step 6.4 tests the connection.

## 3. DigitalOcean

1. **Spaces:** create a private bucket in `syd1`. Turn off the CDN and file listing.
2. **Spaces key:** create an access key limited to that bucket, with read and write.
   Keep the key pair for step 6.
3. **VM:** create a Droplet in `syd1`: Ubuntu 24.04, 2 vCPU / 4 GiB, with your SSH key.
   Turn on monitoring.
4. **Firewall:** create a cloud firewall for the Droplet. Allow inbound TCP 22 from
   your own IP address only. Allow TCP 80 and 443 from anywhere. Allow all outbound.
5. **DNS:** point an `A` record for `<domain>` at the Droplet's IP address. Caddy
   needs it before step 7, to get its certificate.

## 4. VM software

1. Install Docker Engine and the Compose plugin from
   [Docker's Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/).
2. Make Docker start at boot:

   ```console
   sudo systemctl enable --now docker
   ```

3. Clone the repository with a read-only deploy key, and check out `main`.
4. Build the runtime image. Its tag is the commit, so the running image is traceable:

   ```console
   export MARKOS_IMAGE_TAG=$(git rev-parse --short HEAD) MARKOS_DOMAIN=<domain>
   C="docker compose -f infrastructure/compose.production.yml"
   sudo -E $C build gateway
   ```

   Set `MARKOS_IMAGE_TAG`, `MARKOS_DOMAIN` and `C` again in every new shell.

## 5. Site file

1. Create the folder and copy the example:

   ```console
   sudo install -d -m 0755 /etc/markos
   sudo install -m 0644 infrastructure/site.example.json /etc/markos/site.json
   ```

2. Make a new owner ID:

   ```console
   cat /proc/sys/kernel/random/uuid
   ```

3. Edit `/etc/markos/site.json` with `sudo nano`. Replace every placeholder: the domain,
   the owner ID, the Supabase host, the Google client ID and subject, and the bucket.
   The file holds no secrets.

## 6. Secrets, configs and database

The script is `infrastructure/provision.sh`. It runs inside the runtime image and
writes to `/etc/markos`. It prints names, never values.

1. Make the private CA and the server certificate, then every generated secret:

   ```console
   sudo -E infrastructure/provision.sh tls
   sudo -E infrastructure/provision.sh secrets
   ```

2. Store the secrets you collected. Each command asks for one value at a hidden prompt:

   ```console
   sudo -E infrastructure/provision.sh put-secret google-client-secret
   sudo -E infrastructure/provision.sh put-secret spaces-access-key
   sudo -E infrastructure/provision.sh put-secret spaces-secret-key
   ```

3. Copy the Supabase CA from step 2.3 to the VM, then install it:

   ```console
   sudo install -m 0644 <downloaded-ca-file> /etc/markos/tls/supabase-ca.pem
   ```

4. Store the Supabase administrator connection. At the prompt, type one line, with
   your host and password:

   `host=db.<ref>.supabase.co port=5432 dbname=postgres user=postgres password=<pw> sslmode=verify-full sslrootcert=/etc/markos/tls/supabase-ca.pem`

   ```console
   sudo -E infrastructure/provision.sh put-secret supabase-admin-dsn
   ```

5. Write the service configs and the broker definitions:

   ```console
   sudo -E infrastructure/provision.sh configs
   sudo -E infrastructure/provision.sh definitions
   ```

6. Set up the database. This creates the groups and one login per service, applies
   the migrations as `markos_migrator` and adds your owner record:

   ```console
   sudo -E infrastructure/provision.sh database
   ```

7. Delete the administrator connection. No service uses it:

   ```console
   sudo rm /etc/markos/secrets/supabase-admin-dsn
   ```

## 7. Start

```console
sudo -E $C up -d --wait
sudo -E $C ps
```

Every service must show `healthy`. Caddy gets its certificate on the first request.

## 8. Smoke test

1. Open `https://<domain>/app`. Sign in with Google. You reach the app.
2. Start a conversation and send a message. You get a reply.
3. Upload a **synthetic** test bill, never a real one. It appears in the bill list.
4. In the DigitalOcean console, check that one object appeared in the bucket.
5. On your Mac, in this repo, sign in with the CLI. The session folder must be
   private (mode 700):

   ```console
   mkdir -m 700 -p ~/.markos
   M="uv run markos-control --production --origin https://<domain> --session-file $HOME/.markos/s.json"
   $M login --open-browser
   ```

   Sign in with Google, then paste the one-use code the page shows. Then `$M me`
   shows your account, and `$M bills` lists the test bill.

Stop here if a step fails. Record the result in the PR that closes M7-E.

## 9. Recovery copy

Keep an encrypted copy of the secrets in your password manager (owner decision 4):

```console
sudo tar -C /etc/markos -czf - site.json tls secrets | gpg -c > ~/markos-recovery.tgz.gpg
```

Attach `markos-recovery.tgz.gpg` to a password manager entry, store the passphrase
there too, then delete the local file.

## 10. Restore the database

Supabase Pro keeps 7 daily backups. Deleted rows leave Supabase's backups when the
last backup that holds them expires, after at most 7 days. Bill originals in Spaces
have no backup, so an erased original is gone for good.

Each deletion also writes a small tombstone file to Spaces, under
`bills-tombstones/`. After a restore, replay those files, so that deletions made
after the backup stay deleted:

1. Stop MarkOS:

   ```console
   sudo -E $C stop
   ```

2. In the Supabase dashboard, open **Database > Backups** and restore the backup you need.
3. Reapply the tombstones. It prints how many it applied:

   ```console
   sudo -E infrastructure/provision.sh reapply-tombstones
   ```

4. Start MarkOS again, as in step 7.

Data written after the backup is lost. This step only makes sure that deleted
bills and mail records stay deleted.

## Not verified yet

These are checked for the first time when this runbook runs:

1. That the Supabase `postgres` role can create roles and grant `CREATE` on the
   `postgres` database. CI checks the same steps against plain PostgreSQL 17, where
   `postgres` is a superuser.
2. That the containers can reach the Supabase host (step 2.5).
3. That Spaces offers access keys limited to one bucket (step 3.2).
4. That the OAuth Playground shows `sub` as described (step 1.5).

## Known gaps

1. CLI sign-in against production (`--production`, step 8.5) has run only against
   a local check, never a real server.
2. The production issuer keeps the issuer name `synthetic-issuer`. It is an
   internal label stored in every account row, and users never see it. Renaming it
   needs a migration that rewrites those rows, so it stays (owner decision, 2026-11-28).

## Stop and remove

`sudo -E $C down` stops every service and keeps the RabbitMQ and Caddy volumes.
PostgreSQL data stays in Supabase. Delete cloud resources only by hand, in the
provider consoles.
