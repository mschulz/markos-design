# Infrastructure

| Path | Contents |
|---|---|
| `migrations/` | PostgreSQL migrations `0001`–`0019`. Append only: never edit an applied file; the next is `0020`. Applied only through the migration-role API, never at service start. |
| `rabbitmq/` | Broker definitions (no credentials), the test broker config and the pinned version profile. An administrator applies topology; runtime identities cannot. |
| `compose.m0-test.yml` | Disposable CI lifecycle harness (restart, pressure and restore drills). |
| `compose.m0-replay-test.yml` | Disposable CI broker-volume-loss drill. |
| `compose.m1-control-test.yml` | Disposable local and CI preview of the web app; see [docs/local-preview.md](../docs/local-preview.md). |

None of the Compose files is a deployment manifest. The destructive drills run
only on GitHub-hosted runners against uniquely labelled projects. Nothing here
provisions cloud resources.

Reasons for these rules: [docs/decisions.md](../docs/decisions.md).
