"""Explicit role-per-process synthetic lifecycle with independent issuer/tool fixtures."""

import asyncio
import base64
import contextlib
import json
import logging
import os
import signal
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from markos_runtime.api.schema import EXPECTED_MIGRATIONS
from markos_runtime.control.api_config import SyntheticGovernanceApiConfig
from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.control.store import ControlStore
from markos_runtime.control_lifecycle.config import ControlServiceConfig
from markos_runtime.control_lifecycle.issuer import DurableFixtureIssuer, RemoteFixtureIssuer
from markos_runtime.lifecycle.health import HealthServer, HealthState
from markos_runtime.store.database import DurableStore, StorageError
from markos_runtime.tools.transport import FixtureConnection, FixtureTransport
from markos_runtime.tools.worker import GovernedConversationWorker
from markos_runtime.transport.broker import BrokerSession, endpoint
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.management import TopologyAudit
from markos_runtime.transport.publisher import Publisher


def value(environ: Mapping[str, str], name: str) -> str:
    result = environ.get(name, "")
    if not result or len(result) > 8192:
        raise StorageError("explicit fixture credential required")
    return result


def isolated_dsn(raw: str) -> None:
    data = conninfo_to_dict(raw)
    host = str(data.get("host", ""))
    if not (
        host in {"127.0.0.1", "localhost", "::1", "postgres"}
        or host.startswith("/private/tmp/markos-m0b-")
    ) or not str(data.get("dbname", "")).startswith(("markos_test_", "markos_m1g")):
        raise StorageError("owned synthetic database required")


class ControlService:
    def __init__(self, config: ControlServiceConfig, environ: Mapping[str, str]) -> None:
        self.config, self.environ = config, environ
        self.stop = asyncio.Event()
        self.health = HealthState()
        self.epoch: UUID | None = None
        self.instance = uuid4()
        self.http: Any = None
        self.hard_stop: asyncio.TimerHandle | None = None

    def request_stop(self) -> None:
        if self.stop.is_set():
            return
        self.health.draining = True
        self.health.ready = False
        self.stop.set()
        if self.http is not None:
            self.http.should_exit = True
        self.hard_stop = asyncio.get_running_loop().call_later(15, os._exit, 1)

    async def pause(self, seconds: float) -> None:
        with suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self.stop.wait()

    async def probe(self, store: Any) -> UUID:
        if isinstance(store, DurableStore):
            ident = await store.identity()
            if (ident.service_id, ident.owner_id, ident.domain) != (
                self.config.service_id,
                self.config.owner_id,
                self.config.domain,
            ):
                raise StorageError("fixed runtime identity mismatch")
            async with store._transaction() as conn:
                row = await (
                    await conn.execute(
                        "SELECT markos_control.runtime_process_probe(%s) AS epoch",
                        (Jsonb(EXPECTED_MIGRATIONS),),
                    )
                ).fetchone()
            assert row is not None
            epoch = UUID(str(row["epoch"]))
        else:
            row = await store._call(
                "process_probe",
                Jsonb(EXPECTED_MIGRATIONS),
            )
            epoch = UUID(row["epoch"])
        if self.epoch is not None and self.epoch != epoch:
            raise RuntimeError("restored generation requires new process")
        self.epoch = epoch
        return epoch

    async def http_server(self, app: Any, port: int, *, lifespan: bool = False) -> None:
        import uvicorn  # type: ignore[import-not-found]

        class ControlledServer(uvicorn.Server):  # type: ignore[misc]
            @contextlib.contextmanager
            def capture_signals(self) -> Any:
                yield

        self.http = ControlledServer(
            uvicorn.Config(
                app,
                host=self.config.listen_host,
                port=port,
                ssl_certfile=self.config.certificate_file,
                ssl_keyfile=self.config.private_key_file,
                proxy_headers=False,
                access_log=False,
                log_config=None,
                lifespan="on" if lifespan else "off",
                limit_concurrency=48,
                timeout_graceful_shutdown=10,
                timeout_keep_alive=2,
            )
        )
        await self.http.serve()

    async def fixtures(self) -> None:
        cfg, env = self.config, self.environ
        if cfg.component == "issuer":
            from markos_runtime.control_lifecycle.issuer import issuer_app

            issuer = DurableFixtureIssuer(Path(cfg.fixture_state_file), Path(cfg.fixture_seed_file))
            tokens = [
                value(env, x)
                for x in (
                    cfg.issuer_api_secret_ref,
                    cfg.issuer_worker_secret_ref,
                    cfg.issuer_admin_secret_ref,
                )
            ]
            if len(set(tokens)) != 3 or any(len(x) != 43 for x in tokens):
                raise StorageError("separate fixture scopes required")
            app = issuer_app(issuer, cfg.issuer_endpoint, *tokens)
            port = urlsplit(cfg.issuer_endpoint).port
        else:
            from markos_runtime.control_lifecycle.mcp_fixture import mcp_app

            app = mcp_app(
                Path(cfg.fixture_state_file),
                cfg.owner_id,
                cfg.domain,
                cfg.tool_endpoint,
                value(env, cfg.tool_secret_ref),
                value(env, cfg.fixture_admin_secret_ref),
            )
            port = urlsplit(cfg.tool_endpoint).port
        task = asyncio.create_task(
            self.http_server(app, int(port or 0), lifespan=cfg.component == "mcp")
        )
        try:
            while not self.stop.is_set() and not task.done():
                self.health.touch()
                self.health.ready = bool(self.http and self.http.started)
                await self.pause(0.2)
            if task.done() and not self.stop.is_set():
                await task
                raise RuntimeError("fixture process exited")
        finally:
            if self.http:
                self.http.should_exit = True
            await asyncio.wait_for(task, 12)

    async def session(self) -> None:
        cfg, env = self.config, self.environ
        dsn = value(env, cfg.database_secret_ref)
        isolated_dsn(dsn)
        async with AsyncExitStack() as stack:
            store: Any
            if cfg.component == "gateway":
                store = await stack.enter_async_context(ControlStore.connect(dsn))
            else:
                store = await stack.enter_async_context(
                    DurableStore.connect(dsn, max_connections=2)
                )
            await self.probe(store)
            self.health.reconnects += 1
            issuer = None
            consumer = None
            publisher = None
            worker = None
            plain_worker = None
            server_task = None
            if cfg.component in {"gateway", "worker"}:
                issuer = RemoteFixtureIssuer(
                    cfg.issuer_endpoint,
                    Path(cfg.ca_file),
                    value(
                        env,
                        cfg.issuer_api_secret_ref
                        if cfg.component == "gateway"
                        else cfg.issuer_worker_secret_ref,
                    ),
                )
            if cfg.component == "gateway":
                from markos_runtime.api.application import create_app

                key = base64.urlsafe_b64decode(value(env, cfg.session_key_secret_ref))
                app = create_app(
                    SyntheticGovernanceApiConfig(
                        schema_version=1,
                        api_origin=cfg.api_origin,
                        conversation_profile="deterministic-conversation-v1",
                        web_profile="synthetic-web-cli-v1",
                        governance_profile="synthetic-governance-v1",
                    ),
                    store,
                    issuer,  # type: ignore[arg-type]
                    session_key=key,
                    confirm_synthetic=True,
                )
                server_task = asyncio.create_task(
                    self.http_server(app, int(urlsplit(cfg.api_origin).port or 0))
                )
            elif cfg.component == "worker":
                transport = FixtureTransport(
                    FixtureConnection(
                        cfg.tool_endpoint,
                        cfg.owner_id,
                        cfg.domain,
                        Path(cfg.ca_file),
                        value(env, cfg.tool_secret_ref),
                    ),
                    confirm_synthetic=True,
                )
                plain_worker = ConversationWorker(store, issuer, confirm_synthetic=True)  # type: ignore[arg-type]
                worker = GovernedConversationWorker(
                    store, issuer, transport=transport, confirm_synthetic=True
                )
            elif cfg.component in {"publisher", "completion-publisher", "consumer", "activity"}:
                raw = value(env, cfg.broker_secret_ref)
                target = endpoint(raw)
                if (
                    target.host not in {"127.0.0.1", "localhost", "rabbitmq"}
                    or not target.path[1:].startswith("markos-test")
                    or target.scheme != "amqps"
                ):
                    raise StorageError("owned TLS broker required")
                broker = await stack.enter_async_context(BrokerSession.connect(raw))
                if cfg.component in {"publisher", "completion-publisher"}:
                    audit = TopologyAudit(
                        cfg.audit_url,
                        value(env, cfg.audit_user_secret_ref),
                        value(env, cfg.audit_password_secret_ref),
                        broker.vhost,
                    )
                    publisher = Publisher(
                        broker, await broker.channel(publisher=True), store, audit
                    )
                else:
                    producer = (
                        "control-test-agent" if cfg.component == "activity" else "control-gateway"
                    )
                    consumer = Consumer(broker, store, {producer: cfg.broker_publisher_user})
                    await consumer.start()
                    stack.push_async_callback(consumer.stop)
            try:
                while not self.stop.is_set():
                    async with asyncio.timeout(12):
                        epoch = await self.probe(store)
                        if issuer:
                            await issuer.remote("health", {})
                        if cfg.component == "gateway":
                            if server_task and server_task.done():
                                await server_task
                                raise RuntimeError("gateway process exited")
                            self.health.ready = bool(self.http and self.http.started)
                        else:
                            if publisher:
                                if not broker.connection.connected.is_set():
                                    raise StorageError("broker unavailable")
                                await publisher.publish_once()
                            if worker:
                                handled = await worker.run_once()
                                if not handled and plain_worker:
                                    await plain_worker.run_once()
                            if consumer and not consumer.ready.is_set():
                                raise StorageError("broker unavailable")
                            role = (
                                "worker"
                                if cfg.component == "worker"
                                else "publisher"
                                if cfg.component in {"publisher", "completion-publisher"}
                                else "consumer"
                            )
                            await store.heartbeat(
                                component=role,
                                instance_id=self.instance,
                                epoch=epoch,
                                status="ready",
                            )
                            self.health.ready = True
                        self.health.passes += 1
                        self.health.touch()
                    await self.pause(0.25 if worker or publisher else 1)
            finally:
                self.health.ready = False
                if self.http:
                    self.http.should_exit = True
                if server_task:
                    await asyncio.wait_for(server_task, 12)

    async def run(self) -> int:
        async with AsyncExitStack() as stack:
            health = HealthServer(self.health, self.config.health_port)
            await health.start()
            stack.push_async_callback(health.close)
            loop = asyncio.get_running_loop()
            for name in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(name, self.request_stop)
            try:
                if self.config.component in {"issuer", "mcp"}:
                    await self.fixtures()
                else:
                    while not self.stop.is_set():
                        try:
                            await self.session()
                        except RuntimeError:
                            return 1
                        except Exception:
                            self.health.ready = False
                            self.health.faults += 1
                            self.health.touch()
                            await self.pause(1)
                return 0
            finally:
                if self.hard_stop:
                    self.hard_stop.cancel()
                for name in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(name)


def run_control(config: ControlServiceConfig, environ: Mapping[str, str]) -> int:
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(ControlService(config, environ).run())
    except Exception:
        print(json.dumps({"component": "synthetic_control", "event": "startup_failed"}), flush=True)
        return 1
