"""Pinned aio-pika adapter, strict confirm checking and bounded hard-close fallback."""

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

import aio_pika
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection
from aiormq.connection import TCPTransportFactory, TLSTransportFactory, TransportFactory
from pamqp.commands import Basic
from yarl import URL

from markos_runtime.config import ProposedLimits


class TransportError(ValueError):
    """Redacted transport failure; no URL, credentials, payload or broker reply text."""


class AbortableFactory(TransportFactory):
    def __init__(self) -> None:
        self.writer: asyncio.StreamWriter | None = None

    async def create(
        self, url: URL, **kwargs: Any
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        factory = TLSTransportFactory() if url.scheme == "amqps" else TCPTransportFactory()
        reader, self.writer = await factory.create(url, **kwargs)
        return reader, self.writer

    def abort(self) -> None:
        if self.writer is not None:
            self.writer.transport.abort()


def endpoint(dsn: str) -> URL:
    try:
        url = URL(dsn)
        if (
            url.scheme not in {"amqp", "amqps"}
            or not url.host
            or not url.user
            or not url.password
            or url.path in {"", "/"}
            or url.query_string
            or url.fragment
        ):
            raise ValueError
        if url.scheme != "amqps" and url.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError
        return url
    except (ValueError, TypeError):
        raise TransportError("explicit secure broker endpoint required") from None


def positive_confirmation(result: object) -> None:
    if not isinstance(result, Basic.Ack):
        raise TransportError("positive publisher confirmation required")


class BrokerSession:
    def __init__(
        self,
        connection: AbstractRobustConnection,
        factory: AbortableFactory,
        url: URL,
        limits: ProposedLimits | None = None,
    ) -> None:
        self.limits = limits or ProposedLimits()
        self.connection = connection
        self.factory = factory
        self.username = str(url.user)
        self.vhost = url.path[1:]

    @classmethod
    @asynccontextmanager
    async def connect(
        cls, dsn: str, *, limits: ProposedLimits | None = None
    ) -> AsyncIterator[Self]:
        profile = limits or ProposedLimits()
        url = endpoint(dsn)
        factory = AbortableFactory()

        class TrackedConnection(aio_pika.RobustConnection):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.kwargs["transport_factory"] = factory

        try:
            connection = await aio_pika.connect_robust(
                url,
                timeout=profile.connect_seconds,
                heartbeat=profile.heartbeat_seconds,
                reconnect_interval=1,
                fail_fast=True,
                ssl_context=ssl.create_default_context(),
                connection_class=TrackedConnection,
            )
        except Exception:
            factory.abort()
            raise TransportError("broker connection unavailable") from None
        session = cls(connection, factory, url, profile)
        try:
            yield session
        finally:
            await session.close()

    async def channel(self, *, publisher: bool) -> AbstractRobustChannel:
        try:
            async with asyncio.timeout(self.limits.connect_seconds):
                return await self.connection.channel(
                    publisher_confirms=publisher, on_return_raises=publisher
                )
        except Exception:
            raise TransportError("broker channel unavailable") from None

    async def close_channel(
        self, channel: AbstractRobustChannel, seconds: float | None = None
    ) -> None:
        try:
            async with asyncio.timeout(self.limits.close_seconds if seconds is None else seconds):
                await channel.close()
        except Exception:
            self.factory.abort()  # StreamWriter transport abort: no protocol round trip.
            await self.close()

    async def close(self) -> None:
        try:
            async with asyncio.timeout(self.limits.close_seconds):
                await self.connection.close()
        except Exception:
            self.factory.abort()

    async def send(
        self,
        channel: AbstractRobustChannel,
        exchange: str,
        routing_key: str,
        body: bytes,
        message_id: str,
        message_type: str,
    ) -> None:
        try:
            target = await channel.get_exchange(exchange, ensure=False)
            message = aio_pika.Message(
                body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
                message_id=message_id,
                type=message_type,
                user_id=self.username,
            )
            async with asyncio.timeout(self.limits.confirm_seconds):
                result = await target.publish(
                    message,
                    routing_key=routing_key,
                    mandatory=True,
                    timeout=self.limits.confirm_seconds,
                )
            positive_confirmation(result)
        except Exception:
            raise TransportError("publication outcome not confirmed") from None
