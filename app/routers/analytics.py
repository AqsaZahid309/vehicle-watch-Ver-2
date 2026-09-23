import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/fleet")
async def fleet_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    service = AnalyticsService(db)
    return await service.fleet_summary(current_user)


@router.get("/devices/{device_id}")
async def device_trends(
    device_id: uuid.UUID,
    last_n: int = Query(default=100, ge=10, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    service = AnalyticsService(db)
    return await service.device_trends(device_id, current_user, last_n)
