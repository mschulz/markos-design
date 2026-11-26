# Local synthetic preview

A disposable local preview of the web app, CLI, deterministic chat, the approval
flow and (with `--bills`) the electricity-bill screen. It uses synthetic accounts
and data only. It is not a deployment manifest.

## Prerequisites

- A fresh checkout or folder. Setup refuses to overwrite an existing preview's
  private state.
- Python 3.13, uv, OpenSSL and local Docker Compose. No cloud account.
- Free loopback ports: 5672, 15672, 18443, 19443, 19444, 19445.
- PostgreSQL publishes on 5432 by default. If another local database uses it, set
  `MARKOS_PREVIEW_POSTGRES_PORT` to a free port before **every** setup, start and
  stop command. Never stop an existing database to make room.

## Start

From the repo root:

```console
export UV_PROJECT_ENVIRONMENT=/tmp/markos-preview-env
uv sync --locked --extra control --extra tools --group browser
export MARKOS_TEST_COMPOSE_PROJECT=markos-m1g-local-$(openssl rand -hex 6)
uv run --no-sync python -m tests.control_process.support prepare --confirm-synthetic --bills
docker compose -p "$MARKOS_TEST_COMPOSE_PROJECT" -f infrastructure/compose.m1-control-test.yml up -d --wait postgres rabbitmq
uv run --no-sync python -m tests.control_process.support bootstrap --confirm-synthetic
docker compose -p "$MARKOS_TEST_COMPOSE_PROJECT" -f infrastructure/compose.m1-control-test.yml build gateway
uv run --no-sync python -m tests.control_process.support start --confirm-synthetic
```

Omit `--bills` for a preview without the electricity-bill screen. The project
name must match `markos-m1g-local-` plus 12 hex characters; reuse the same value
for later start and stop commands.

## Sign in

Open `https://127.0.0.1:18443/app/sign-in` in a browser profile that trusts only
the disposable test CA, `infrastructure/.test-certs/ca.pem`. Keep that CA out of
normal trust stores. The certificate expires after two days; recreate the preview
rather than extending it.

Sign-in codes are in `infrastructure/.m1g-preview/controller.json` (ignored by
Git): `both` is administrator and operator, `approver` approves, `viewer` reads.
Use a separate browser profile for the approver. Never put a code in a URL, shell
argument, screenshot or shared feedback.

The CLI (`markos-control --help`) uses the same HTTPS API and keeps its session
files in an explicit private folder outside Git.

## Stop

```console
uv run --no-sync python -m tests.control_process.support stop --confirm-synthetic
```

This destroys the preview's synthetic database and broker volumes only. The
private fixture folder is kept. Never use it as a backup of real data.
