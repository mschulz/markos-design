---
title: MarkOS v0.3 M1-G CI and Lean Preview
type: implementation-record
status: implemented-synthetic-preview
version: 1.0
owner: Mark Schulz
created: 2026-11-13
updated: 2026-11-13
reviewed: 2026-11-13
tags:
  - architecture
---

# MarkOS v0.3 M1-G CI and Lean Preview

## Lean delivery decision and scope

The owner asked for startup-style lean delivery and early MVP feedback while this slice was being built. The priority is a runnable **synthetic product preview**, essential regression CI and an honest evidence/gap ledger. This refines the broader M1-G proposal in [[MarkOS v0.3 M1 Control Plane Implementation Plan]]; it does **not** declare the entire M1 synthetic fault matrix complete. Full operational closeout remains open. Real authentication/providers/models, personal data, paid or consequential tools, cloud deployment and merging remain separately authorized work.

GitHub remains canonical for project artifacts; PostgreSQL remains authoritative for MarkOS operational state, RabbitMQ carries internal events, source originals belong in access-controlled object storage and Markdown is derived. No PARA, Obsidian or archived `markos` dependency is introduced. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[ADR-005 Governed MCP Access for Agents]] and [[ADR-006 Control Plane Identity and Trust Boundary]] still apply. Native Gmail/Pub/Sub and rules-first M3 decisions are unchanged.

## Main review after PR 24

Reviewed main `c590cb27914ffa5ab37c5e77b9f951e730bb0258` against PR 24 head `55726a15d101c2e4f166d69c68040bbc67b08085`: identical tree `0d2445052f20ff6973d752a562b35289df07a527`. The post-merge [runtime workflow](https://github.com/mschulz/markos-design/actions/runs/43344966029) and [documentation workflow](https://github.com/mschulz/markos-design/actions/runs/43344966025) succeeded. The existing required runtime matrix covers 882 cases; seven documentation tests run separately. The main review found no architecture reset or history-preservation regression. Three closeout gaps are addressed here:

- A stalled final normal HTTP response send could outlive the request-work deadline. Final response transmission now has a two-second bound, including release of local admission.
- Blocking standard-library HTTP reads could survive cancellation while a peer dripped bytes within the idle timeout. The shared absolute-deadline socket watchdog shuts down the captured socket, including `Connection: close` responses whose socket is detached from `HTTPConnection`. Tests prove the actual blocking reader thread terminates for header, body and line drips. CLI, MCP fixture transport and the new remote issuer use this bound.
- The 16 short-request limit was process-local. Additive migration **0011** gives gateway replicas a shared 16-request lease budget, with 15-second crash expiry and bounded cleanup on request completion. SSE retains its separate 1/session, 4/principal, 20/global admission; health retains local admission. Migrations 0001–0010, all prior dependency pins and archived/imported work remain byte-for-byte unchanged.

## Runnable fixture processes

The independent optional `Dockerfile.control` installs the existing locked `control` and `tools` extras plus packaged web assets. The base M0 Dockerfile and base dependency boundary remain intact. No fixture controller, tests, migration role, database dump or embedded credential enters the image.

`markos-runtime serve-control-synthetic --confirm-synthetic --config ABSOLUTE_FILE` accepts only the closed `control-process-fixture-v1` test profile. It never migrates or discovers configuration/secrets. Gateway uses its narrow pool (four connections); runtime roles use two. The launcher checks exact migration fingerprints and fixed service/owner/domain mapping, fences epoch changes, reconnects dependencies, exposes private loopback health and drains on SIGTERM within the 15-second hard stop. Each worker checks governed work first, then ordinary deterministic chat. The two workers still share the existing two execution leases; no model or new effect adapter exists.

The isolated issuer persists only its **external synthetic identity fixture state** in an explicitly supplied private folder: signing key, bounded test accounts, one-use codes, refresh state and revocations. Its API, worker and controller credentials have distinct scopes; worker credentials cannot mint tokens or change revocation. The separate MCP SDK fixture persists a bounded synthetic operation ledger before returning a result. Its hold/ledger controller uses a separate credential, cannot be selected through a label/tool argument and is unreachable through the production-facing fixture transport. Neither fixture store is a replacement for PostgreSQL knowledge/conversation authority. These are test substitutes for external systems, never real IdP or provider adapters.

The Compose file is a **disposable local/CI preview**, not a deployment manifest. Gateway, issuer, MCP fixture and internal roles share an owned network namespace so the previously reviewed numeric-loopback HTTPS resource rule stays intact. Only explicitly published loopback ports are reachable from the host. Roles retain separate credentials, containers and caps. Gateway gets 0.5 CPU/256 MiB; eight other fixture/runtime roles each 0.25 CPU/256 MiB; PostgreSQL 0.5 CPU/512 MiB and RabbitMQ 1 CPU/512 MiB. Total ceilings are 4 CPUs and 3.25 GiB; these are disposable acceptance caps, **not a measured production sizing recommendation**. Containers are non-root, read-only, capability-free, PID/file bounded, and receive no Docker socket. A one-time owned test fixture initialization sets private folder ownership before non-root startup.

Runtime access/debug logs are disabled; Docker log persistence is disabled for this preview. Private health contains only fixed coarse counters/readiness and a process instance. CI publishes only coarse timing/count measurements, never raw HTTP, login state, environment, snapshots or fixture ledgers. Automated 14-day redacted log retention, monitoring/alert delivery and daily cleanup scheduling are **deferred**, not claimed by size rotation or an ad hoc timer.

## Try the preview after review

Prerequisites: a separate checkout, Python 3.13, uv, OpenSSL and local Docker Compose. No cloud account is needed. The commands below are instructions for the owner; this PR does not start a service on the owner's machine. Use a fresh checkout/folder if a previous preview exists: setup refuses to overwrite its private state. Ports 5432, 5672, 15672, 18443, 19443 and 19444 must be free. The fixture TLS certificate expires after two days; recreate the disposable preview rather than treating it as production PKI.

From `services/runtime`:

```console
export UV_PROJECT_ENVIRONMENT=/tmp/markos-preview-env
uv sync --locked --extra control --extra tools --group browser
export MARKOS_TEST_COMPOSE_PROJECT=markos-m1g-local-012345abcdef
uv run --no-sync python -m tests.control_process.support prepare --confirm-synthetic
docker compose -p "$MARKOS_TEST_COMPOSE_PROJECT" -f ../../infrastructure/compose.m1-control-test.yml up -d --wait postgres rabbitmq
uv run --no-sync python -m tests.control_process.support bootstrap --confirm-synthetic
docker compose -p "$MARKOS_TEST_COMPOSE_PROJECT" -f ../../infrastructure/compose.m1-control-test.yml build gateway
uv run --no-sync python -m tests.control_process.support start --confirm-synthetic
```

Open `https://127.0.0.1:18443/app/sign-in` using a browser profile that trusts only this disposable test CA, `infrastructure/.test-certs/ca.pem`. Fixture credentials are in the local private `infrastructure/.m1g-preview/controller.json`, excluded from Git: `both` is administrator/operator, `approver` approves, and `viewer` reads. Enter a credential in the sign-in form; do not put credentials in a URL, shell argument, screenshot or shared feedback. Keep this synthetic CA out of normal production trust stores.

A useful first feedback pass takes about ten minutes: sign in as `both`; send a message and inspect the durable checksum reply; inspect Agents and health, Activity and Configuration; submit a synthetic tool request; sign in as `approver` in a separate profile and inspect the exact preview before approving. Try a narrow screen and keyboard navigation. Record confusing labels, missing status, unnecessary steps and the next useful real task. Approval/admin/operator roles remain distinct. This preview demonstrates control flow, not agent intelligence or a Gmail classifier.

The CLI uses the same HTTPS API. `markos-control --help` describes commands and explicit private session files outside Git; onboard through the fixture web authorization page. It never connects directly to SQL/RabbitMQ. Setup creates one enabled test agent/conversation and an exact approval-required fixture grant; this is privileged synthetic bootstrap, never an API bypass available to a normal user.

Stop and remove only the recorded disposable project's services/volumes:

```console
uv run --no-sync python -m tests.control_process.support stop --confirm-synthetic
```

That explicitly destroys **synthetic preview databases/broker volumes only**; it preserves the local private fixture folder for reference. Do not reuse it as a real-data backup. No general erase/provision/deploy command is added.

## Verification and recovery limits

Existing M0 lock/schema/type/wheel, real PostgreSQL/RabbitMQ, broker-volume-loss and lifecycle/restore jobs remain required. The separate optional API/browser tests remain required. A seventh check adds the independent optional image, real TLS issuer/MCP fixture processes, browser/mobile and CLI preview, durable chat, approval waiting across process restarts, one-call unknown outcome after worker SIGKILL, PostgreSQL/RabbitMQ interruption and a quiesced snapshot restored into a fresh database. The hosted process job requires its services/browser and runs without skipped integration cases. Locally Docker is unavailable in the authoring environment; its process/image evidence must come from that hosted check.

The restore drill keeps independent issuer/MCP state current, rotates the SQL epoch **before writers**, reapplies explicit current synthetic deny facts using the migration role, invalidates old sessions/approvals and verifies retained history plus unknown-outcome evidence without repeating fixture calls. The controller is outside the image, checks local Docker context and owned project/container labels and never restarts the daemon. Snapshot bytes stay in memory and are never published. This is evidence for the synthetic path, not a product backup agent or current revocation/deletion service. It does not recover history absent from the snapshot, reverse external effects or claim zero RPO/RTO.

`config/runtime/control/m1-closeout.json` maps every M1-01–M1-22 and MCP-01–MCP-10 to existing/new test families and explicit gaps. **Overall M1 operational closeout and production readiness remain false.** In particular: all process kill points/slow-reader and proxy/resource measurements, independent durable real deletion/revocation feed, daily 7/365-day cleanup and 24-hour alert delivery, 14-day diagnostic operations, production onboarding/OAuth/secret custody, real provider idempotency/reconciliation, cloud sizing/restore/monitoring and MacBook-off evidence remain unpassed. Existing controlled-clock retention tests are evidence for SQL policy, not a running maintenance service.

## Next review decision

Review this bounded preview PR, then collect product feedback before expanding infrastructure or starting another milestone. Do not block local synthetic feedback on production-only gates, and do not waive applicable safety gates before connecting real accounts. Decide the first useful end-to-end task and whether to prioritize UI changes or the separately reviewed native M2 vertical slice. No merge, deployment or real integration is implied.
