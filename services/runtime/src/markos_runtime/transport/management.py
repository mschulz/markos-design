"""Read-only live topology audit. No runtime declaration, binding or admin mutation."""

import asyncio
import base64
import json
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from markos_runtime.transport.broker import TransportError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise TransportError("management redirects forbidden")


class TopologyAudit:
    def __init__(self, base_url: str, username: str, password: str, vhost: str) -> None:
        url = urlsplit(base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})
        ):
            raise TransportError("explicit secure management endpoint required")
        if not username or not password or not vhost:
            raise TransportError("explicit management identity required")
        self.base = base_url.rstrip("/")
        self.vhost = vhost
        self._authorization = (
            "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        )

    def _request(self, path: str) -> Any:
        request = Request(
            self.base + "/api/" + path, headers={"Authorization": self._authorization}
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=5) as response:
                body = response.read(262145)
            if len(body) > 262144:
                raise ValueError
            return json.loads(body)
        except Exception:
            raise TransportError("live topology audit unavailable") from None

    async def get(self, *parts: str) -> Any:
        return await asyncio.to_thread(self._request, "/".join(quote(x, safe="") for x in parts))

    @staticmethod
    def validate_queue(
        queue: dict[str, Any], *, max_messages: int = 10000, max_bytes: int = 33554432
    ) -> None:
        args = {**(queue.get("effective_policy_definition") or {}), **queue.get("arguments", {})}
        # Policy keys and AMQP argument names differ; enforce whichever is present.
        overflow_values = [args[k] for k in ("x-overflow", "overflow") if k in args]
        if any(value != "reject-publish" for value in overflow_values):
            raise TransportError("queue overflow safety mismatch")
        maximum = args.get("x-max-length", args.get("max-length"))
        size = args.get("x-max-length-bytes", args.get("max-length-bytes"))
        overflow = args.get("x-overflow", args.get("overflow"))
        if (
            queue.get("durable") is not True
            or queue.get("auto_delete") is not False
            or queue.get("exclusive", False) is not False
            or queue.get("type") != "classic"
            or type(maximum) is not int
            or not 1 <= maximum <= max_messages
            or type(size) is not int
            or not 65536 <= size <= max_bytes
            or overflow != "reject-publish"
            or any(k in args for k in ["x-message-ttl", "message-ttl", "x-expires", "expires"])
        ):
            raise TransportError("queue safety profile mismatch")

    async def verify(
        self,
        domain: str,
        consumers: tuple[str, ...],
        *,
        review: bool = False,
        max_messages: int = 10000,
        max_bytes: int = 33554432,
    ) -> None:
        exchange = f"markos.{domain}.review" if review else f"markos.{domain}.events"
        key = "v1.review.requested" if review else "v1.foundation.noop-requested"
        queues = (
            {f"markos.{domain}.operator-review"}
            if review
            else {f"markos.{domain}.{consumer}" for consumer in consumers}
        )
        if not queues:
            raise TransportError("intended consumer set is empty")
        descriptor = await self.get("exchanges", self.vhost, exchange)
        if (
            descriptor.get("type") != "topic"
            or descriptor.get("durable") is not True
            or descriptor.get("auto_delete") is not False
            or descriptor.get("internal") is not False
            or descriptor.get("arguments", {}).get("alternate-exchange") is not None
            or descriptor.get("policy") is not None
            or descriptor.get("operator_policy") is not None
        ):
            raise TransportError("exchange safety profile mismatch")
        bindings = await self.get("exchanges", self.vhost, exchange, "bindings", "source")
        actual = {
            (b.get("destination"), b.get("routing_key"), b.get("destination_type"))
            for b in bindings
        }
        if actual != {(q, key, "queue") for q in queues}:
            raise TransportError("intended subscriber bindings mismatch")
        for queue in queues:
            self.validate_queue(
                await self.get("queues", self.vhost, queue),
                max_messages=max_messages,
                max_bytes=max_bytes,
            )
