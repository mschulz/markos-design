"""GitHub-hosted disposable Compose acceptance. No local/remote production control."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from markos_runtime.execution.scheduler import Scheduler
from markos_runtime.store.database import DurableStore
from tests import lifecycle_support as h


def restore_fixture(
    backup: bytes,
    objects: dict[str, str],
    old_dispatch: Any,
    old_task: Any,
    executor: Any,
    old_review: Any,
) -> tuple[Path, str]:
    database = "markos_m0f_restored"
    with psycopg.connect(h.admin("postgres"), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER markos_migrator").format(sql.Identifier(database))
        )
    h.compose(
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "-U",
        "postgres",
        "--no-owner",
        "--role",
        "markos_migrator",
        "-d",
        database,
        body=backup,
    )
    with psycopg.connect(
        make_conninfo(h.admin(database), user="markos_migrator"), autocommit=True
    ) as conn:
        row = conn.execute("SELECT markos_runtime.fence_restored_state()").fetchone()
        assert row
        assert row[0] != old_dispatch.epoch

    async def fence_checks() -> None:
        async with (
            DurableStore.connect(h.dsn("publisher", database)) as pub,
            DurableStore.connect(h.dsn("consumer", database)) as cons,
        ):
            assert not await pub.record_publication(
                old_dispatch.event_id, old_dispatch.fence, epoch=old_dispatch.epoch
            )
            assert not await cons.finish_task(old_task, executor)
            assert not await cons.finish_review(old_review)

    asyncio.run(fence_checks())
    folder = h.ROOT / "infrastructure/.test-artifacts"
    folder.mkdir(exist_ok=True)
    # Simulated old object backup includes a deleted object; never reactivate that source.
    for source in h.SOURCES:
        body = objects[str(source)].encode()
        with psycopg.connect(h.admin(database)) as conn:
            row = conn.execute(
                "SELECT sha256,deleted_at FROM markos_runtime.source_observations "
                "WHERE source_id=%s",
                (source,),
            ).fetchone()
            assert row and sha256(body).hexdigest() == row[0]
        target = folder / (source.hex + ".synthetic")
        if row[1] is None:
            target.write_bytes(body)
        else:
            target.unlink(missing_ok=True)
    assert (folder / (h.SOURCES[0].hex + ".synthetic")).read_bytes() == h.SOURCE_BODY
    assert not (folder / (h.SOURCES[1].hex + ".synthetic")).exists()
    services = {}
    for app in h.APPS:
        role = (
            "audit"
            if app.startswith("audit-")
            else ("consumer" if app in {"consumer", "worker", "review"} else "publisher")
        )
        services[app] = {
            "environment": {
                "MARKOS_RUNTIME_DATABASE_DSN": (
                    f"host=postgres dbname={database} user={h.LOGINS[role]} "
                    "password=synthetic-test-only"
                )
            }
        }
    override = folder / "restored-compose.json"
    override.write_text(json.dumps({"services": services}))
    return override, database


def test_lifecycle_restart_pressure_restore_and_clean_runtime() -> None:
    if os.environ.get("MARKOS_REQUIRE_LIFECYCLE_TEST") != "1":
        pytest.skip("dedicated disposable lifecycle CI job only")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        pytest.fail("lifecycle fault control requires an isolated GitHub-hosted runner")
    if (
        os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        != "unix:///var/run/docker.sock"
    ):
        pytest.fail("remote Docker control is forbidden")
    h.project()
    measurements: dict[str, Any] = {}
    h.phase("cold_start")
    h.wait(lambda: all(h.ready(app) for app in h.APPS), 120)
    with psycopg.connect(h.admin()) as conn:
        assert conn.execute("SELECT count(*) FROM markos_runtime.task_results").fetchone()[0] >= 4  # type: ignore[index]

    async def disable() -> None:
        async with DurableStore.connect(h.dsn("publisher")) as pub:
            assert await Scheduler(pub).set_enabled(h.SCHEDULE, 1, False)

    asyncio.run(disable())
    for app in h.APPS:
        meta = json.loads(h.command(["docker", "inspect", h.container(app)]))[0]
        assert (
            meta["HostConfig"]["Memory"] == 128 * 1024 * 1024
            and meta["HostConfig"]["PidsLimit"] == 64
        )
        assert (
            meta["HostConfig"]["ReadonlyRootfs"]
            and meta["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
        )
        assert not meta["HostConfig"]["PortBindings"]
        packages = h.compose(
            "exec",
            "-T",
            app,
            "/opt/runtime/.venv/bin/python",
            "-I",
            "-c",
            'import importlib.util;assert importlib.util.find_spec("markos") is None;'
            'assert importlib.util.find_spec("duckdb") is None;'
            'assert importlib.util.find_spec("pytest") is None',
        )
        assert packages == b""
    h.phase("hard_worker_exit")
    before = h.health("worker")["instance"]
    meta = json.loads(h.command(["docker", "inspect", h.container("worker")]))[0]
    started = datetime.fromisoformat(meta["State"]["StartedAt"].replace("Z", "+00:00"))
    time.sleep(max(0, 11 - (datetime.now(UTC) - started).total_seconds()))
    item = h.emit("hard-exit")
    h.wait(lambda: h.running(item.event_id))
    h.compose(
        "exec",
        "-T",
        "worker",
        "/opt/runtime/.venv/bin/python",
        "-c",
        'import os,signal; ids=[]\nfor x in os.listdir("/proc"):\n'
        " if x.isdigit() and int(x) not in {1,os.getpid()}:\n"
        "  try:\n"
        '   a=open("/proc/"+x+"/cmdline","rb").read().split(b"\\0")\n'
        '   if b"serve-synthetic" in a:ids.append(int(x))\n'
        "  except OSError:pass\n"
        "assert len(ids)==1\nos.kill(ids[0],signal.SIGKILL)",
    )
    h.wait(lambda: h.ready("worker") and h.health("worker")["instance"] != before)
    h.wait(lambda: h.results(item.event_id) == 2)
    h.phase("graceful_shutdown")
    item = h.emit("signal-stop")
    h.wait(lambda: h.running(item.event_id))
    start = time.monotonic()
    h.compose("stop", "--timeout", "15", "worker")
    measurements["graceful_shutdown_seconds"] = round(time.monotonic() - start, 3)
    meta = json.loads(h.command(["docker", "inspect", h.container("worker")]))[0]
    assert meta["State"]["ExitCode"] == 0
    h.compose("up", "-d", "--no-build", "worker")
    h.wait(lambda: h.results(item.event_id) == 2)
    h.phase("broker_outage")
    h.compose("stop", "rabbitmq")
    h.wait(lambda: not h.health("publisher")["ready"], 35)
    item = h.emit("broker-outage")
    assert h.results(item.event_id) == 0
    h.compose("up", "-d", "--wait", "rabbitmq")
    h.wait(lambda: h.results(item.event_id) == 2)
    h.phase("database_outage")
    h.compose("stop", "postgres")
    h.wait(lambda: not h.health("worker")["ready"], 35)
    h.compose("up", "-d", "--wait", "postgres")
    h.wait(lambda: all(h.ready(app) for app in h.APPS), 120)
    item = h.emit("database-recovered")
    h.wait(lambda: h.results(item.event_id) == 2)
    for alarm, set_value, restore in [
        ("memory", "1", "268435456"),
        ("disk", "1000000000000000", "268435456"),
    ]:
        h.phase(alarm + "_pressure")
        args = (
            ["set_vm_memory_high_watermark", "absolute"]
            if alarm == "memory"
            else ["set_disk_free_limit"]
        )
        h.compose("exec", "-T", "rabbitmq", "rabbitmqctl", *args, set_value)
        try:
            item = h.emit(alarm + "-pressure")
            time.sleep(2)
            assert h.results(item.event_id) == 0
            with psycopg.connect(h.admin()) as conn:
                assert conn.execute(
                    "SELECT state FROM markos_runtime.outbox WHERE event_id=%s", (item.event_id,)
                ).fetchone()[0] in {"pending", "leased"}  # type: ignore[index]
        finally:
            h.compose("exec", "-T", "rabbitmq", "rabbitmqctl", *args, restore)
        start = time.monotonic()
        h.wait(lambda item=item: h.results(item.event_id) == 2)
        measurements[alarm + "_backlog_drain_seconds"] = round(time.monotonic() - start, 3)
    h.phase("docker_daemon_restart")
    h.command(["sudo", "systemctl", "restart", "docker"], timeout=60)
    h.wait(lambda: all(h.ready(app) for app in h.APPS), 120)
    item = h.emit("daemon-recovered")
    h.wait(lambda: h.results(item.event_id) == 2)
    stats = h.command(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            *[h.container(app) for app in h.APPS + ["rabbitmq"]],
        ]
    )
    measurements["container_samples"] = [
        {"memory": x["MemUsage"], "pids": x["PIDs"]}
        for x in map(json.loads, stats.decode().splitlines())
    ]
    h.phase("isolated_backup_restore")
    h.compose("stop", "--timeout", "15", *h.APPS)
    # Stopped workers leave a controlled leased task/publication/review in the backup.
    item = h.emit("restored-work")
    executor = uuid4()

    async def claims() -> tuple[Any, Any, Any]:
        async with (
            DurableStore.connect(h.dsn("publisher")) as pub,
            DurableStore.connect(h.dsn("consumer")) as cons,
        ):
            await cons.handoff(item)
            dispatch = (await pub.claim_outbox())[0]
            task = await cons.claim_task(executor)
            assert task
            await cons.review_delivery(b"synthetic review", "invalid")
            review = (await cons.claim_reviews())[0]
            return dispatch, task, review

    dispatch, task, review = asyncio.run(claims())
    backup = h.compose(
        "exec", "-T", "postgres", "pg_dump", "-U", "postgres", "--no-owner", "-Fc", "markos_m0f"
    )
    assert len(backup) < 20 * 1024 * 1024
    folder = h.ROOT / "infrastructure/.test-artifacts"
    folder.mkdir(exist_ok=True)
    backup_path = folder / "database.dump"
    backup_path.write_bytes(backup)
    manifest_path = folder / "backup-manifest.json"
    manifest_path.write_text(json.dumps({"sha256": sha256(backup).hexdigest()}))
    expected = json.loads(manifest_path.read_text())["sha256"]
    backup = backup_path.read_bytes()
    assert sha256(backup).hexdigest() == expected
    assert sha256(backup + b"corruption").hexdigest() != expected
    objects = {str(source): h.SOURCE_BODY.decode() for source in h.SOURCES}
    override, database = restore_fixture(backup, objects, dispatch, task, executor, review)
    start = time.monotonic()
    h.compose("up", "-d", "--no-build", "--wait", *h.APPS, override=override, timeout=180)
    h.wait(lambda: h.results(item.event_id, database) == 2)
    measurements["restore_to_result_seconds"] = round(time.monotonic() - start, 3)
    with psycopg.connect(h.admin(database)) as conn:
        assert conn.execute("SELECT count(*) FROM markos_runtime.restore_history").fetchone() == (
            1,
        )
    for app in h.APPS:
        logs = h.compose("logs", "--no-color", app).decode()
        assert "synthetic-test-only" not in logs and "PRIVATE_PAYLOAD" not in logs
    folder = h.ROOT / "infrastructure/.test-artifacts"
    folder.mkdir(exist_ok=True)
    (folder / "measurements.json").write_text(json.dumps(measurements, indent=2) + "\n")
    h.phase("complete")
