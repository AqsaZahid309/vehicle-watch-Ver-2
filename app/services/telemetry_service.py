import json
import math
import uuid
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import events
from app.core.exceptions import ConflictError
from app.core.utils import as_utc, utcnow
from app.models.device import Device
from app.models.telemetry import Telemetry
from app.models.user import User
from app.schemas.telemetry import (
    BatchIngestResponse,
    PaginatedTelemetry,
    TelemetryCreate,
    TelemetryRead,
)
from app.services.access import get_org_device
from app.services.device_service import latest_cache_key
from app.core.metrics import TELEMETRY_INGESTED

settings = get_settings()

_CACHE_FIELDS = [
    "gps_lat", "gps_lon", "engine_temp", "rpm", "fuel_level", "battery_voltage",
    "speed", "vibration", "oil_pressure", "coolant_level", "tire_pressure",
    "ambient_temp", "dtc_codes",
]


def _cache_payload(record: Telemetry) -> dict:
    return {
        "id": str(record.id),
        "device_id": str(record.device_id),
        "recorded_at": as_utc(record.recorded_at).isoformat(),
        **{f: getattr(record, f) for f in _CACHE_FIELDS},
    }


class TelemetryService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis) -> None:
        self._db = db
        self._redis = redis

    async def _after_ingest(self, device: Device, newest: Telemetry, count: int) -> None:
        """Update last_seen, the latest-reading cache and the live event stream."""
        newest_at = as_utc(newest.recorded_at)
        last_seen = as_utc(device.last_seen_at)
        is_newest = last_seen is None or newest_at >= last_seen

        if is_newest:
            await self._db.execute(
                update(Device).where(Device.id == device.id).values(last_seen_at=newest_at)
            )
            # Cache the latest reading with a 5-minute TTL. Only readings newer than
            # what we have are cached, so a backfilled offline buffer never replaces
            # the vehicle's current position.
            payload = _cache_payload(newest)
            await self._redis.setex(latest_cache_key(device.id), 300, json.dumps(payload, default=str))
            await events.publish(self._redis, device.organization_id, "telemetry", payload)

        TELEMETRY_INGESTED.inc(count)

    async def ingest_for_device(self, device: Device, data: TelemetryCreate) -> TelemetryRead:
        recorded_at = data.recorded_at or utcnow()
        if data.recorded_at is not None:
            dup = await self._db.execute(
                select(Telemetry.id).where(
                    Telemetry.device_id == device.id, Telemetry.recorded_at == recorded_at
                )
            )
            if dup.scalar_one_or_none():
                raise ConflictError("A reading with this recorded_at already exists for the device")

        record = Telemetry(
            device_id=device.id,
            recorded_at=recorded_at,
            **data.model_dump(exclude={"recorded_at"}),
        )
        self._db.add(record)
        await self._db.flush()
        await self._db.refresh(record)
        await self._after_ingest(device, record, 1)
        return TelemetryRead.model_validate(record)

    async def ingest_batch_for_device(
        self, device: Device, readings: list[TelemetryCreate]
    ) -> BatchIngestResponse:
        """
        Store an offline buffer in one round trip. Readings whose (device, recorded_at)
        already exist are skipped, so a device can safely re-send a batch whose
        response it never received.
        """
        now = utcnow()
        stamped: dict[datetime, TelemetryCreate] = {}
        for r in readings:
            ts = r.recorded_at or now
            stamped.setdefault(ts, r)  # also drops duplicates inside the batch

        existing = await self._db.execute(
            select(Telemetry.recorded_at).where(
                Telemetry.device_id == device.id,
                Telemetry.recorded_at.in_(list(stamped.keys())),
            )
        )
        existing_ts = {as_utc(ts) for ts in existing.scalars().all()}

        records = [
            Telemetry(device_id=device.id, recorded_at=ts, **r.model_dump(exclude={"recorded_at"}))
            for ts, r in sorted(stamped.items())
            if as_utc(ts) not in existing_ts
        ]
        if records:
            self._db.add_all(records)
            await self._db.flush()
            await self._after_ingest(device, records[-1], len(records))

        return BatchIngestResponse(accepted=len(records), duplicates=len(readings) - len(records))

    async def ingest(self, device_id: uuid.UUID, data: TelemetryCreate, requester: User) -> TelemetryRead:
        device = await get_org_device(self._db, device_id, requester)
        return await self.ingest_for_device(device, data)

    async def ingest_batch(
        self, device_id: uuid.UUID, readings: list[TelemetryCreate], requester: User
    ) -> BatchIngestResponse:
        device = await get_org_device(self._db, device_id, requester)
        return await self.ingest_batch_for_device(device, readings)

    async def get_history(
        self,
        device_id: uuid.UUID,
        requester: User,
        page: int = 1,
        page_size: int = 50,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> PaginatedTelemetry:
        await get_org_device(self._db, device_id, requester)

        conditions = [Telemetry.device_id == device_id]
        if start:
            conditions.append(Telemetry.recorded_at >= start)
        if end:
            conditions.append(Telemetry.recorded_at <= end)

        total = (await self._db.execute(select(func.count()).where(*conditions))).scalar_one()

        # Newest first for time-series dashboards
        result = await self._db.execute(
            select(Telemetry)
            .where(*conditions)
            .order_by(Telemetry.recorded_at.desc())
            .offset((page - 1) * page_size)
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
