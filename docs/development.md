# Development

The application is the package `markos_runtime` in `src/`, with tests in `tests/`.
Python 3.13 and uv. Design rules and their reasons are in
[decisions.md](decisions.md).

Entry points:

- `markos-runtime`: `preflight-control` (validation only, no connections), `serve-synthetic` and `serve-control-synthetic` (synthetic test
  services, require `--confirm-synthetic`).
- `markos-control`: the CLI client. It uses the HTTPS API only.

Optional installs: the `control` extra (API, web, auth), the `tools` extra (MCP
client) and the `browser` dependency group (Playwright tests). Keep them out of
the base environment; its isolation tests require them absent.

## Checks

From the repository root, with an environment outside the repository:

```console
export UV_PROJECT_ENVIRONMENT=/tmp/markos-runtime-env
uv sync --locked
uv lock --check
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest --ignore=tests/control_api --ignore=tests/browser --ignore=tests/control_process --ignore=tests/integration/test_volume_loss.py --ignore=tests/integration/test_lifecycle_harness.py
uv run --no-sync python -m markos_runtime.schemas --output-dir config/runtime/schemas
python3 -B scripts/check_docs.py
```

The schemas command fails if the committed JSON schemas differ from the models.
Add `--write` to regenerate them. `check_docs.py` fails on broken local links in
Markdown files.

## Database and broker tests

They skip locally unless you point them at disposable services:

- `MARKOS_TEST_ADMIN_DSN`: a dedicated local PostgreSQL 17 cluster. The tests
  create and drop their own `markos_test_*` databases and create roles. Never use
  a shared, live or preview database.
- `MARKOS_TEST_RABBIT_HTTP`, `MARKOS_TEST_AMQP_BASE`, `MARKOS_TEST_RABBIT_USER`,
  `MARKOS_TEST_RABBIT_PASSWORD`: a disposable local RabbitMQ.
- `MARKOS_TEST_S3_ENDPOINT`, `MARKOS_TEST_S3_ACCESS_KEY`,
  `MARKOS_TEST_S3_SECRET_KEY`: a disposable local S3 store on a loopback address,
  for the Spaces adapter tests. Start SeaweedFS with the test key in
  `infrastructure/seaweedfs/s3-test.json`, as the `control-api` CI job does.

CI sets `MARKOS_REQUIRE_*` flags so that a missing service fails instead of
skipping. The API, browser, process, volume-loss and lifecycle suites run in
their own CI jobs; see `.github/workflows/runtime-contracts.yml`.

To check the optional `control` profile on its own:
`uv sync --locked --extra control --no-dev`, then
`uv run --no-sync python scripts/check_control_profile.py`.

## Local preview

See [local-preview.md](local-preview.md).
