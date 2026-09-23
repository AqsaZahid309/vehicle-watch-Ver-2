import uuid
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.rate_limiter import check_device_rate_limit
from app.database import get_db
from app.dependencies import WRITE_ROLES, get_current_user, get_device_from_api_key, get_optional_user
from app.models.device import Device
from app.models.user import User
from app.redis import get_redis
from app.schemas.telemetry import (
    BatchIngestResponse, PaginatedTelemetry, TelemetryBatch, TelemetryCreate, TelemetryRead,
)
from app.services.telemetry_service import TelemetryService

router = APIRouter(tags=["Telemetry"])


def _resolve_writer(
    device_id: uuid.UUID,
    device: Device | None,
    user: User | None,
) -> tuple[Device | None, User | None]:
    """Accept either the device's own X-Device-Key or a user bearer token with write access."""
    if device is not None:
        if device.id != device_id:
            raise ForbiddenError("This device key belongs to a different device")
        return device, None
    if user is None:
        raise UnauthorizedError("Provide a Bearer token or an X-Device-Key header")
    if user.role not in WRITE_ROLES:
        raise ForbiddenError("Read-only users cannot ingest telemetry")
    return None, user


@router.post(
    "/devices/{device_id}/telemetry",
    response_model=TelemetryRead,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_telemetry(
    device_id: uuid.UUID,
    data: TelemetryCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    key_device: Device | None = Depends(get_device_from_api_key),
    user: User | None = Depends(get_optional_user),
) -> TelemetryRead:
    device, user = _resolve_writer(device_id, key_device, user)
    # Rate limit is enforced per device (not per user) — a rogue device cannot
    # flood the system regardless of the credentials behind it.
    await check_device_rate_limit(str(device_id), redis)
    service = TelemetryService(db, redis)
    if device is not None:
        return await service.ingest_for_device(device, data)
    return await service.ingest(device_id, data, user)


@router.post(
    "/devices/{device_id}/telemetry/batch",
    response_model=BatchIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_telemetry_batch(
    device_id: uuid.UUID,
    batch: TelemetryBatch,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    key_device: Device | None = Depends(get_device_from_api_key),
    user: User | None = Depends(get_optional_user),
) -> BatchIngestResponse:
    """Upload up to 500 buffered readings (with their own `recorded_at`) in one request. Idempotent."""
    device, user = _resolve_writer(device_id, key_device, user)
    await check_device_rate_limit(str(device_id), redis)
    service = TelemetryService(db, redis)
    if device is not None:
        return await service.ingest_batch_for_device(device, batch.readings)
    return await service.ingest_batch(device_id, batch.readings, user)


@router.post("/ingest/telemetry", response_model=TelemetryRead, status_code=status.HTTP_201_CREATED)
async def device_ingest(
    data: TelemetryCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    device: Device | None = Depends(get_device_from_api_key),
) -> TelemetryRead:
    """Device gateway endpoint: the device is identified by its `X-Device-Key` alone."""
    if device is None:
        raise UnauthorizedError("X-Device-Key header required")
    await check_device_rate_limit(str(device.id), redis)
    return await TelemetryService(db, redis).ingest_for_device(device, data)


@router.post("/ingest/telemetry/batch", response_model=BatchIngestResponse, status_code=status.HTTP_201_CREATED)
async def device_ingest_batch(
    batch: TelemetryBatch,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    device: Device | None = Depends(get_device_from_api_key),
) -> BatchIngestResponse:
    if device is None:
        raise UnauthorizedError("X-Device-Key header required")
    await check_device_rate_limit(str(device.id), redis)
    return await TelemetryService(db, redis).ingest_batch_for_device(device, batch.readings)


@router.get("/devices/{device_id}/telemetry", response_model=PaginatedTelemetry)
async def get_telemetry_history(
    device_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=1000),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> PaginatedTelemetry:
    service = TelemetryService(db, redis)
    return await service.get_history(device_id, current_user, page, page_size, start, end)
