"""
Real-time event bus on Redis pub/sub.

Every organization has one channel. The API publishes telemetry, alerts,
notifications and work-order changes; the SSE endpoint subscribes and streams
them to browsers. Redis pub/sub fans out across API replicas and the separate
worker process, which in-process queues could not do.

Publishing is best-effort: a Redis hiccup must never fail telemetry ingestion.
"""

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def org_channel(org_id: uuid.UUID | str) -> str:
    return f"vw:events:{org_id}"


async def publish(redis: Any, org_id: uuid.UUID | str, event_type: str, data: dict[str, Any]) -> None:
    if redis is None:
        return
    try:
        await redis.publish(
            org_channel(org_id),
            json.dumps({"type": event_type, "data": data}, default=str),
        )
    except Exception as exc:  # pragma: no cover — best effort
        logger.debug("Event publish failed (%s): %s", event_type, exc)
