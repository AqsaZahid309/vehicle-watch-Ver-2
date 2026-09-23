import uuid

from fastapi import APIRouter, Depends, Query, status
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import check_device_rate_limit
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.redis import get_redis
from app.schemas.telemetry import TelemetryCreate, TelemetryRead, PaginatedTelemetry
from app.services.telemetry_service import TelemetryService

router = APIRouter(prefix="/devices", tags=["Telemetry"])


@router.post(
    "/{device_id}/telemetry",
    response_model=TelemetryRead,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_telemetry(
    device_id: uuid.UUID,
    data: TelemetryCreate,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> TelemetryRead:
    # Rate limit is enforced per device (not per user) — a rogue device cannot
    # flood the system regardless of the authenticated user behind it.
    await check_device_rate_limit(str(device_id), redis)
    service = TelemetryService(db, redis)
    return await service.ingest(device_id, data, current_user)


@router.get("/{device_id}/telemetry", response_model=PaginatedTelemetry)
async def get_telemetry_history(
    device_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> PaginatedTelemetry:
    service = TelemetryService(db, redis)
    return await service.get_history(device_id, current_user, page, page_size)
