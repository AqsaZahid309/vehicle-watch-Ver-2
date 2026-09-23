import json
import uuid
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.redis import get_redis
from app.schemas.ml import DeviceForecast
from app.services.access import get_org_device
from app.services.analytics_service import AnalyticsService
from app.services.forecast_service import ForecastService

router = APIRouter(prefix="/analytics", tags=["Analytics"])

_FLEET_FORECAST_CACHE_TTL = 60


@router.get("/fleet")
async def fleet_summary(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return await AnalyticsService(db).fleet_summary(current_user)


@router.get("/timeline")
async def alert_timeline(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return await AnalyticsService(db).alert_timeline(current_user, days)


@router.get("/forecast", response_model=list[DeviceForecast])
async def fleet_forecast(
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> list[DeviceForecast]:
    """Failure forecast for every active vehicle, riskiest first (cached for 60 s)."""
    key = f"vw:forecast:fleet:{current_user.organization_id}"
    try:
        cached = await redis.get(key)
        if cached:
            return [DeviceForecast.model_validate(x) for x in json.loads(cached)]
    except Exception:
        pass
    result = await ForecastService(db).forecast_fleet(current_user.organization_id)
    try:
        await redis.setex(key, _FLEET_FORECAST_CACHE_TTL,
                          json.dumps([f.model_dump(mode="json") for f in result]))
    except Exception:
        pass
    return result


@router.get("/devices/{device_id}")
async def device_trends(
    device_id: uuid.UUID,
    last_n: int = Query(default=100, ge=10, le=2000),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return await AnalyticsService(db).device_trends(device_id, current_user, last_n)


@router.get("/devices/{device_id}/hourly")
async def device_hourly(
    device_id: uuid.UUID,
    hours: int = Query(default=72, ge=1, le=24 * 90),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return await AnalyticsService(db).device_hourly(device_id, current_user, hours)


@router.get("/devices/{device_id}/forecast", response_model=DeviceForecast)
async def device_forecast(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> DeviceForecast:
    """Remaining-useful-life projection for each health signal of one vehicle."""
    device = await get_org_device(db, device_id, current_user)
    return await ForecastService(db).forecast_device(device)
