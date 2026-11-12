"""One bounded publishing pass; PostgreSQL owns durable retries and original IDs."""

import asyncio
import random
from datetime import UTC, datetime

from aio_pika.abc import AbstractRobustChannel

from markos_runtime.config import ProposedLimits
from markos_runtime.contracts.base import ContractError
from markos_runtime.store.database import DurableStore, OutboxClaim
from markos_runtime.transport.broker import BrokerSession, TransportError
from markos_runtime.transport.management import TopologyAudit


def backoff(attempts: int) -> int:
    return random.randint(1, min(300, 2 ** min(max(attempts, 0) + 1, 9)))


class Publisher:
    def __init__(
        self,
        session: BrokerSession,
        channel: AbstractRobustChannel,
        store: DurableStore,
        audit: TopologyAudit,
        *,
        limits: ProposedLimits | None = None,
    ) -> None:
        if session.vhost != audit.vhost:
            raise TransportError("broker/audit namespace mismatch")
        self.session, self.channel, self.store, self.audit = session, channel, store, audit
        self.limits: ProposedLimits = limits or getattr(session, "limits", ProposedLimits())
        self._inflight = asyncio.Semaphore(self.limits.max_inflight_confirms)

    async def _one(self, claim: OutboxClaim) -> bool:
        code = "topology"
        try:
            async with self._inflight, asyncio.timeout(25):
                if len(claim.event.to_wire()) > self.limits.max_message_bytes:
                    await self.store.review_delivery(claim.event.to_wire(), "oversize")
                    await self.store.hold_for_review(claim, "oversize")
                    return False
                claim.event.require_live(datetime.now(UTC))
                await self.audit.verify(
                    claim.event.domain,
                    claim.consumers,
                    max_messages=self.limits.queue_max_messages,
                    max_bytes=self.limits.queue_max_bytes,
                )
                code = "confirm"
                await self.session.send(
                    self.channel,
                    f"markos.{claim.event.domain}.events",
                    "v1.foundation.noop-requested",
                    claim.event.to_wire(),
                    str(claim.event_id),
                    claim.event.event_type,
                )
                return await self.store.record_publication(claim.event_id, claim.fence)
        except ContractError:
            code = "expired"
            await self.store.review_delivery(claim.event.to_wire(), "expired")
            await self.store.hold_for_review(claim, "expired")
            return False
        except (TransportError, TimeoutError):
            pass
        # Unknown outcomes remain recoverable, not dispatched. No immediate nack/requeue loop.
        await self.store.retry_claim(claim, backoff(claim.attempts), code)
        return False

    async def publish_once(self) -> int:
        claims = await self.store.claim_outbox(limit=min(10, self.limits.max_inflight_confirms))
        return sum(await asyncio.gather(*(self._one(c) for c in claims)))

    async def publish_reviews_once(self) -> int:
        count = 0
        for claim in await self.store.claim_reviews(limit=1):
            ref = claim.reference
            try:
                async with self._inflight, asyncio.timeout(25):
                    await self.audit.verify(
                        ref.domain,
                        (),
                        review=True,
                        max_messages=self.limits.queue_max_messages,
                        max_bytes=self.limits.queue_max_bytes,
                    )
                    await self.session.send(
                        self.channel,
                        f"markos.{ref.domain}.review",
                        "v1.review.requested",
                        ref.model_dump_json().encode(),
                        str(ref.review_id),
                        "transport.review.reference",
                    )
                    count += await self.store.finish_review(claim)
            except (TransportError, TimeoutError):
                await self.store.finish_review(claim, backoff(claim.attempts))
        return count
