# Infrastructure

| Path | Contents |
|---|---|
| `migrations/` | PostgreSQL migrations `0001`–`0024`. Append only: never edit an applied file; the next is `0025`. Applied only through the migration-role API, never at service start. |
| `seaweedfs/` | The synthetic access key for the disposable SeaweedFS S3 store that the Spaces adapter tests use. Test only. |
| `rabbitmq/` | Broker definitions (no credentials), the test and production broker configs and the pinned version profile. An administrator applies topology; runtime identities cannot. |
| `caddy/` | The production Caddyfile: the only public listener, forwarding HTTPS to the gateway. |
| `compose.m0-test.yml` | Disposable CI lifecycle harness (restart, pressure and restore drills). |
| `compose.m0-replay-test.yml` | Disposable CI broker-volume-loss drill. |
| `compose.m1-control-test.yml` | Disposable local and CI preview of the web app; see [docs/local-preview.md](../docs/local-preview.md). |
| `compose.production.yml` | The M7 production profile: Caddy, RabbitMQ and the MarkOS services on one VM, with Supabase off the VM. Its header lists the host files it needs. See [docs/m7-production.md](../docs/m7-production.md). |
| `compose.production-ci.yml` | CI only: boots `compose.production.yml` against a TLS PostgreSQL stand-in for Supabase. |

Only `compose.production.yml` is a deployment manifest, and nothing deploys it yet. The destructive drills run
only on GitHub-hosted runners against uniquely labelled projects. Nothing here
provisions cloud resources.

Reasons for these rules: [docs/decisions.md](../docs/decisions.md).
