"""
Server-Sent Events stream of live fleet events (telemetry, alerts, notifications,
work-order changes) for the caller's organization.

Browsers' EventSource cannot send an Authorization header, and putting a JWT in
the URL would leak it into proxy and access logs. Instead the client exchanges
its token for a single-use ticket valid for 60 seconds and opens the stream
with `?ticket=`.
"""

import asyncio
import secrets
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.events import org_channel
from app.core.exceptions import UnauthorizedError
from app.dependencies import get_current_user
from app.models.user import User
from app.redis import get_redis

router = APIRouter(prefix="/stream", tags=["Live stream"])

_TICKET_TTL = 60
_KEEPALIVE_SECONDS = 15


def _ticket_key(ticket: str) -> str:
    return f"vw:stream:ticket:{ticket}"


@router.post("/ticket")
async def create_ticket(
    user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    ticket = secrets.token_urlsafe(24)
    await redis.setex(_ticket_key(ticket), _TICKET_TTL, str(user.organization_id))
    return {"ticket": ticket, "expires_in": _TICKET_TTL}


async def _event_source(request: Request, redis: aioredis.Redis, org_id: str) -> AsyncIterator[str]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(org_channel(org_id))
    try:
        yield "retry: 3000\n\n"
        yield 'event: ready\ndata: {"type":"ready"}\n\n'
        idle = 0.0
        while True:
            if await request.is_disconnected():
                break
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                idle = 0.0
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield f"data: {data}\n\n"
            else:
                idle += 1.0
                if idle >= _KEEPALIVE_SECONDS:
                    idle = 0.0
                    yield ": keepalive\n\n"
                await asyncio.sleep(0)
    finally:
        await pubsub.unsubscribe(org_channel(org_id))
        await pubsub.aclose()


@router.get("")
async def stream(
    request: Request,
    ticket: str = Query(..., min_length=10),
    redis: aioredis.Redis = Depends(get_redis),
) -> StreamingResponse:
    org_id = await redis.getdel(_ticket_key(ticket))
    if not org_id:
        raise UnauthorizedError("Invalid or expired stream ticket")
    return StreamingResponse(
        _event_source(request, redis, org_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
