"""Explicit CI-only destruction of this test project's broker volume, not PostgreSQL."""

import asyncio
import json
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError

import psycopg
import pytest

from markos_runtime.execution.worker import Worker
from markos_runtime.recovery.service import Recovery
from markos_runtime.store.database import DurableStore
from markos_runtime.transport.broker import BrokerSession
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.publisher import Publisher
from tests.integration.test_rabbitmq import Harness, until
from tests.integration.test_recovery import clock
from tests.integration.test_storage import count, event

pytest_plugins = ["tests.integration.test_rabbitmq"]


def remove_test_broker_volume() -> None:
    project = os.environ.get("MARKOS_TEST_COMPOSE_PROJECT", "")
    if not re.fullmatch(r"markos-m0e-[0-9]+-[0-9]+", project):
        pytest.fail("volume-loss test requires its own disposable CI Compose project")
    config = Path(__file__).resolve().parents[4] / "infrastructure/compose.m0-replay-test.yml"
    prefix = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(args: list[str]) -> str:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120).stdout

    volume = project + "_rabbitmq-data"
    meta = json.loads(run(["docker", "volume", "inspect", volume]))[0]
    assert meta["Labels"]["com.docker.compose.project"] == project
    assert meta["Labels"]["com.docker.compose.volume"] == "rabbitmq-data"
    run(prefix + ["stop", "rabbitmq"])
    run(prefix + ["rm", "-f", "rabbitmq"])
    run(["docker", "volume", "rm", volume])  # Exact owned volume, no wildcard or force.
    # A new empty volume is created; the independent PostgreSQL volume is untouched.
    run(prefix + ["up", "-d", "--wait", "--wait-timeout", "120", "rabbitmq"])


def test_confirmed_broker_volume_loss_recovers_every_missing_consumer(
    database: dict[str, str], rabbit: Harness
) -> None:
    if os.environ.get("MARKOS_REQUIRE_VOLUME_TEST") != "1":
        pytest.skip("dedicated disposable Compose volume-loss job only")

    async def scenario() -> None:
        item = event()
        async with (
            DurableStore.connect(database["publisher"]) as pub,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            await pub.append(item)
            publisher = Publisher(
                session, await session.channel(publisher=True), pub, rabbit.auditor()
            )
            assert await publisher.publish_once() == 1
        # One subscriber finishes before loss, the other queue still holds a confirmed delivery.
        async with (
            DurableStore.connect(database["consumer"]) as cons,
            BrokerSession.connect(rabbit.dsn("consumer")) as session,
        ):
            consumer = Consumer(session, cons, {"foundation-generator": rabbit.users["publisher"]})
            await consumer.start()
            try:
                await until(lambda: count(database, "inbox") == 1)
                assert await Worker(cons).run_once()
            finally:
                await consumer.stop()
        assert count(database, "task_results") == 1
        await asyncio.to_thread(remove_test_broker_volume)

        def management_ready() -> bool:
            try:
                rabbit.request("GET", "vhosts")
                return True
            except URLError:
                return False

        await until(management_ready, seconds=30)
        # Prove the old namespace is absent before restoring only reviewed topology/permissions.
        assert rabbit.vhost not in {v["name"] for v in rabbit.request("GET", "vhosts")}
        assert count(database, "events") == count(database, "task_results") == 1
        rabbit.setup()
        with psycopg.connect(database["admin"]) as conn:
            due = conn.execute("SELECT replay_after FROM markos_runtime.events").fetchone()
            assert due is not None
        clock(database, due[0] + timedelta(seconds=1))
        async with (
            DurableStore.connect(database["publisher"]) as pub,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            assert await Recovery(pub).reconcile_once() == 1
            publisher = Publisher(
                session, await session.channel(publisher=True), pub, rabbit.auditor()
            )
            assert await publisher.publish_once() == 1
        for role in ["consumer", "audit"]:
            async with (
                DurableStore.connect(database[role]) as store,
                BrokerSession.connect(rabbit.dsn(role)) as session,
            ):
                consumer = Consumer(
                    session, store, {"foundation-generator": rabbit.users["publisher"]}
                )
                await consumer.start()
                try:
                    await until(
                        lambda role=role: (
                            count(database, "inbox") == (1 if role == "consumer" else 2)
                        )
                    )
                    assert await Worker(store).run_once() == (role == "audit")
                finally:
                    await consumer.stop()
        assert count(database, "task_results") == count(database, "tasks") == 2
        with psycopg.connect(database["admin"]) as conn:
            assert conn.execute(
                "SELECT count(*) FROM markos_runtime.event_consumers WHERE handed_off_at IS NULL"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT bool_and(suppress_effects) FROM markos_runtime.replay_history"
            ).fetchone() == (True,)

    asyncio.run(scenario())
