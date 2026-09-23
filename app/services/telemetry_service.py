import json
import math
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import NotFoundError, ForbiddenError
from app.models.device import Device
from app.models.telemetry import Telemetry
from app.models.user import User, UserRole
from app.schemas.telemetry import TelemetryCreate, TelemetryRead, PaginatedTelemetry

settings = get_settings()

_TELEMETRY_FIELDS = [
    "gps_lat", "gps_lon", "engine_temp", "rpm",
    "fuel_level", "battery_voltage", "speed", "vibration",
]


class TelemetryService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis) -> None:
        self._db = db
        self._redis = redis

    async def _get_device_or_raise(self, device_id: uuid.UUID, requester: User) -> Device:
        result = await self._db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if not device:
            raise NotFoundError("Device", str(device_id))
        if requester.role != UserRole.ADMIN and device.owner_id != requester.id:
            raise ForbiddenError("You do not have access to this device")
        return device

    async def ingest(
        self, device_id: uuid.UUID, data: TelemetryCreate, requester: User
    ) -> TelemetryRead:
        await self._get_device_or_raise(device_id, requester)

        record = Telemetry(
            device_id=device_id,
            recorded_at=datetime.now(timezone.utc),
            **data.model_dump(),
        )
        self._db.add(record)
        await self._db.flush()
        await self._db.refresh(record)

        # Cache the latest telemetry in Redis with 5-minute TTL.
        # We cache *after* the flush (record has UUID) but *before* commit —
        # if the commit fails the stale cache will expire naturally.
        cache_key = f"device:{device_id}:latest"
        payload = {
            "id": str(record.id),
            "device_id": str(record.device_id),
            "recorded_at": record.recorded_at.isoformat(),
            **{f: getattr(record, f) for f in _TELEMETRY_FIELDS},
        }
        await self._redis.setex(cache_key, 300, json.dumps(payload))

        return TelemetryRead.model_validate(record)

    async def get_history(
        self,
        device_id: uuid.UUID,
        requester: User,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedTelemetry:
        await self._get_device_or_raise(device_id, requester)

        # Count query
        count_result = await self._db.execute(
            select(func.count()).where(Telemetry.device_id == device_id)
        )
        total = count_result.scalar_one()

        # Data query — newest first for time-series dashboards
        offset = (page - 1) * page_size
        result = await self._db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.recorded_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        items = list(result.scalars().all())

        return PaginatedTelemetry(
            items=[TelemetryRead.model_validate(t) for t in items],
            total=total,
            page=page,
            page_size=page_size,
            pages=max(1, math.ceil(total / page_size)),
        )
