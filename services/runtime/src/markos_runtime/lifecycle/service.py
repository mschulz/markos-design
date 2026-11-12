"""One role per supervised process, explicit secrets, bounded passes and shutdown."""

import asyncio
import json
import logging
import os
import signal
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from uuid import UUID

from markos_runtime.backoff import backoff
from markos_runtime.contracts.policy import Manifest
from markos_runtime.execution.scheduler import Scheduler
from markos_runtime.execution.worker import Worker
from markos_runtime.lifecycle.config import ServiceConfig
from markos_runtime.lifecycle.health import HealthServer, HealthState
from markos_runtime.recovery.service import Recovery
from markos_runtime.store.database import DurableStore, StorageError, TaskClaim
from markos_runtime.transport.broker import BrokerSession, TransportError, endpoint
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.management import TopologyAudit
from markos_runtime.transport.publisher import Publisher


class FatalServiceError(RuntimeError):
    pass


def emit(code: str) -> None:
    # Only fixed call-site codes; never stringify exceptions, config or identifiers.
    print(json.dumps({"component": "synthetic_runtime", "event": code}), flush=True)


def secret(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value or len(value) > 8192:
        raise StorageError("explicit service credential missing")
    return value


class SyntheticWorker(Worker):
    def __init__(self, store: DurableStore, delay: float) -> None:
        super().__init__(store)
        self.delay = delay

    async def _execute(self, claim: TaskClaim) -> None:
        await super()._execute(claim)
        await asyncio.sleep(self.delay)


class Service:
    def __init__(
        self, config: ServiceConfig, manifest: Manifest, environ: Mapping[str, str]
    ) -> None:
        self.config, self.manifest, self.environ = config, manifest, environ
        self.health = HealthState()
        self.stop = asyncio.Event()
        self.epoch: UUID | None = None
        self.exit_code = 0
        self._hard_stop: asyncio.TimerHandle | None = None

    def request_stop(self) -> None:
        if self.stop.is_set():
            return
        self.health.ready = False
        self.health.draining = True
        self.stop.set()
        self._hard_stop = asyncio.get_running_loop().call_later(
            self.config.shutdown_seconds, os._exit, 1
        )

    async def _pause(self, seconds: float) -> None:
        with suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self.stop.wait()

    async def _session(self) -> None:
        cfg = self.config
        from psycopg.conninfo import conninfo_to_dict

        dsn = secret(self.environ, cfg.database_secret_ref)
        info = conninfo_to_dict(dsn)
        host = str(info.get("host", ""))
        if not (
            host in {"127.0.0.1", "localhost", "::1", "postgres"}
            or host.startswith("/private/tmp/markos-m0b-")
        ) or not str(info.get("dbname", "")).startswith(("markos_test_", "markos_m0f")):
            raise FatalServiceError("isolated synthetic database required")
        if cfg.component in {"publisher", "consumer", "review"}:
            target = endpoint(secret(self.environ, cfg.broker_secret_ref))
            if target.host not in {"127.0.0.1", "localhost", "::1", "rabbitmq"} or not target.path[
                1:
            ].startswith("markos-test"):
                raise FatalServiceError("isolated synthetic broker required")
        async with AsyncExitStack() as stack:
            store = await stack.enter_async_context(
                DurableStore.connect(
                    secret(self.environ, cfg.database_secret_ref), max_connections=2
                )
            )
            identity = await store.identity()
            if (identity.service_id, identity.owner_id, identity.domain) != (
                cfg.service_id,
                cfg.owner_id,
                cfg.domain,
            ):
                raise FatalServiceError("identity mismatch")
            epoch = await store.current_epoch()
            if self.epoch is not None and epoch != self.epoch:
                raise FatalServiceError("restored generation requires new process")
            self.epoch = epoch
            publisher: Publisher | None = None
            consumer: Consumer | None = None
            session: BrokerSession | None = None
            if cfg.component in {"publisher", "consumer", "review"}:
                session = await stack.enter_async_context(
                    BrokerSession.connect(secret(self.environ, cfg.broker_secret_ref))
                )
                if cfg.component == "consumer":
                    consumer = Consumer(session, store, cfg.publisher_users)
                    await consumer.start()
                    stack.push_async_callback(consumer.stop)
                else:
                    audit = TopologyAudit(
                        cfg.audit_url,
                        secret(self.environ, cfg.audit_user_secret_ref),
                        secret(self.environ, cfg.audit_password_secret_ref),
                        session.vhost,
                    )
                    targets = tuple(
                        sorted(
                            {
                                s.service_id
                                for s in self.manifest.subscriptions
                                if (s.owner_id, s.domain) == (cfg.owner_id, cfg.domain)
                            }
                        )
                    )
                    await audit.verify(
                        cfg.domain,
                        targets if cfg.component == "publisher" else (),
                        review=cfg.component == "review",
                    )
                    publisher = Publisher(
                        session, await session.channel(publisher=True), store, audit
                    )
            worker = SyntheticWorker(store, cfg.synthetic_work_seconds)
            self.health.reconnects += 1
            emit("connected")
            cleanup_at = 0.0
            try:
                while not self.stop.is_set():
                    async with asyncio.timeout(30):
                        if await store.current_epoch() != epoch:
                            raise FatalServiceError("restored generation")
                        ready = True
                        if cfg.component == "publisher":
                            self.health.ready = False
                            assert publisher is not None
                            await publisher.publish_once()
                            ready = not publisher.last_pass_failed
                        elif cfg.component == "review":
                            self.health.ready = False
                            assert publisher is not None
                            await publisher.publish_reviews_once()
                            ready = not publisher.last_pass_failed
                        elif cfg.component == "consumer":
                            assert consumer is not None and session is not None
                            if consumer.failure is not None:
                                raise FatalServiceError("consumer stopped")
                            ready = (
                                consumer.ready.is_set() and session.connection.connected.is_set()
                            )
                        elif cfg.component == "worker":
                            await worker.run_once()
                            if worker.failed:
                                raise FatalServiceError("worker stopped")
                        elif cfg.component == "scheduler":
                            await Scheduler(store).scan_once()
                        elif cfg.component == "recovery":
                            await Recovery(store).reconcile_once()
                            now = asyncio.get_running_loop().time()
                            if cfg.cleanup_enabled and now >= cleanup_at:
                                await Recovery(store).cleanup_once(dry_run=False)
                                cleanup_at = now + 86400
                        if await store.current_epoch() != epoch:
                            raise FatalServiceError("restored generation")
                        await store.heartbeat(
                            cfg.component,
                            UUID(self.health.instance),
                            epoch,
                            "ready" if ready else "degraded",
                        )
                        self.health.ready = ready
                        self.health.passes += 1
                        self.health.touch()
                    await self._pause(cfg.tick_seconds)
            finally:
                self.health.ready = False
                with suppress(StorageError):
                    await store.heartbeat(
                        cfg.component, UUID(self.health.instance), epoch, "draining"
                    )

    async def _watchdog(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(1)
            if not self.health.live:
                emit("watchdog_expired")
                os._exit(1)

    async def run(self) -> int:
        server = HealthServer(self.health, self.config.health_port)
        await server.start()
        loop = asyncio.get_running_loop()

        def background_failure(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
            emit("background_failure")
            self.exit_code = 1
            self.request_stop()

        loop.set_exception_handler(background_failure)
        for name in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(name, self.request_stop)
        attempts = 0
        watchdog = asyncio.create_task(self._watchdog())
        try:
            while not self.stop.is_set():
                task = asyncio.create_task(self._session())
                stopping = asyncio.create_task(self.stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        {task, stopping}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if stopping in done:
                        # Let a healthy in-flight pass commit and record draining first.
                        _, pending = await asyncio.wait({task}, timeout=3)
                        if pending:
                            task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                        break
                    await task
                    attempts = 0
                except FatalServiceError:
                    emit("fatal")
                    return 1
                except (StorageError, TransportError, TimeoutError, OSError):
                    self.health.ready = False
                    self.health.faults += 1
                    self.health.touch()
                    emit("dependency_unavailable")
                    await self._pause(min(30, backoff(attempts)))
                    attempts += 1
                finally:
                    stopping.cancel()
                    with suppress(asyncio.CancelledError):
                        await stopping
            emit("stopped")
            return self.exit_code
        finally:
            self.health.ready = False
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog
            await server.close()
            if self._hard_stop is not None:
                self._hard_stop.cancel()
            for name in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(name)


def run_service(config: ServiceConfig, manifest: Manifest, environ: Mapping[str, str]) -> int:
    # Third-party connection logs may include endpoint details. Emit only the fixed codes above.
    for namespace in ("aio_pika", "aiormq", "psycopg", "psycopg.pool"):
        logger = logging.getLogger(namespace)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
    try:
        return asyncio.run(Service(config, manifest, environ).run())
    except Exception:
        emit("fatal")
        return 1
