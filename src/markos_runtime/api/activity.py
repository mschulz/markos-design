"""Durable scoped activity pagination and bounded SSE; no process-local event history."""

import asyncio
import json
import time
from contextlib import suppress
from typing import Any
from uuid import UUID

from anyio import CancelScope  # type: ignore[import-not-found]

from markos_runtime.api.application import ApiFault, SyntheticApi, wire
from markos_runtime.contracts.base import Domain


def cursor(
    api: SyntheticApi, binding: dict[str, Any], owner: UUID, domain: Domain, epoch: str, after: int
) -> str:
    return api.codec.seal(
        json.dumps(
            {
                "resource": "activity-v1",
                "principal_id": binding["principal_id"],
                "owner_id": str(owner),
                "domain": domain,
                "epoch": epoch,
                "after": after,
                "expires": int(time.time()) + 600,
            }
        )
    )


def resume(
    api: SyntheticApi,
    token: str | None,
    binding: dict[str, Any],
    owner: UUID,
    domain: Domain,
    head: dict[str, Any],
) -> int:
    if token is None:
        return 0
    try:
        value = json.loads(api.codec.open(token))
        if (
            set(value) == {"principal_id", "owner_id", "domain", "after", "expires"}
            and value.get("principal_id") == binding["principal_id"]
            and value.get("owner_id") == str(owner)
            and value.get("domain") == domain
        ):
            raise ApiFault("resync-required", 409)
        if (
            set(value)
            != {"resource", "principal_id", "owner_id", "domain", "epoch", "after", "expires"}
            or value["resource"] != "activity-v1"
            or value["principal_id"] != binding["principal_id"]
            or value["owner_id"] != str(owner)
            or value["domain"] != domain
            or type(value["after"]) is not int
            or not 0 <= value["after"] <= 9223372036854775807
            or type(value["expires"]) is not int
        ):
            raise ValueError
    except ApiFault:
        raise
    except Exception:
        raise ApiFault("invalid", 400) from None
    if (
        value["expires"] <= time.time()
        or value["epoch"] != head["epoch"]
        or value["after"] > head["head"]
    ):
        raise ApiFault("resync-required", 409)
    return int(value["after"])


async def snapshot(api: SyntheticApi, ctx: Any, owner: UUID, domain: Domain) -> dict[str, Any]:
    return dict(
        await api.store._call("activity_head", ctx.session_id, ctx.lookup_hash, owner, domain)
    )


async def activity_page(
    api: SyntheticApi,
    request: Any,
    ctx: Any,
    binding: dict[str, Any],
    owner: UUID,
    domain: Domain,
    params: dict[str, str],
) -> dict[str, Any]:
    head = await snapshot(api, ctx, owner, domain)
    after = resume(api, params.get("cursor"), binding, owner, domain, head)
    raw = params.get("limit", "50")
    if not raw.isascii() or not raw.isdigit() or not 1 <= len(raw) <= 3 or not 1 <= int(raw) <= 100:
        raise ApiFault("invalid", 400)
    rows = await api.store.read_activity(ctx, owner, domain, after=after, limit=int(raw))
    next_id = cursor(
        api, binding, owner, domain, head["epoch"], rows[-1].sequence if rows else after
    )
    return {"items": wire(rows), "next_cursor": next_id}


async def open_stream(api: SyntheticApi, request: Any) -> Any:
    from fastapi.responses import StreamingResponse  # type: ignore[import-not-found]

    async with asyncio.timeout(10):
        params = api.query(request, {"owner_id", "domain", "cursor"})
        owner, domain = api.scope(params)
        ctx, binding, _ = await api.authenticate(request)
        head = await snapshot(api, ctx, owner, domain)
        after = resume(
            api,
            request.headers.get("last-event-id") or params.get("cursor"),
            binding,
            owner,
            domain,
            head,
        )
        stream_id = await api.store._call(
            "open_activity_stream", ctx.session_id, ctx.lookup_hash, owner, domain
        )

    async def generate() -> Any:
        nonlocal after, binding
        started = time.monotonic()
        checked = 0.0
        total = 0
        try:
            while time.monotonic() - started < 600:
                async with asyncio.timeout(10):
                    current, current_binding, _ = await api.authenticate(request)
                    if current != ctx or not await api.store._call(
                        "renew_activity_stream", ctx.session_id, ctx.lookup_hash, stream_id
                    ):
                        return
                    current_head = await snapshot(api, ctx, owner, domain)
                    if current_head["epoch"] != head["epoch"] or after > current_head["head"]:
                        yield b"event: resync-required\ndata: {}\n\n"
                        return
                    binding = current_binding
                    checked = time.monotonic()
                    rows = await api.store.read_activity(ctx, owner, domain, after=after, limit=100)
                for item in rows:
                    if time.monotonic() - checked >= 5:
                        async with asyncio.timeout(10):
                            await api.authenticate(request)
                            if not await api.store._call(
                                "renew_activity_stream", ctx.session_id, ctx.lookup_hash, stream_id
                            ):
                                return
                        checked = time.monotonic()
                    event_id = cursor(api, binding, owner, domain, head["epoch"], item.sequence)
                    data = json.dumps(wire(item), ensure_ascii=True, separators=(",", ":"))
                    frame = (
                        "id: " + event_id + "\nevent: activity\ndata: " + data + "\n\n"
                    ).encode()
                    if len(frame) > 8192:
                        yield b"event: resync-required\ndata: {}\n\n"
                        return
                    if total + len(frame) > 262000:
                        return
                    total += len(frame)
                    yield frame
                    after = item.sequence
                yield b": keepalive\n\n"
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:
            # No authority, dependency details or payload after failed reauthorization.
            return
        finally:
            with CancelScope(shield=True), suppress(Exception):
                async with asyncio.timeout(5):
                    await api.store._call(
                        "close_activity_stream", ctx.session_id, ctx.lookup_hash, stream_id
                    )

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )
