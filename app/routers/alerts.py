import uuid
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_operator
from app.models.alert import AlertFeedback, AlertSeverity, FaultType
from app.models.user import User
from app.redis import get_redis
from app.schemas.alert import (
    AlertAcknowledge, AlertDetail, AlertFeedbackIn, AlertRead, BulkAcknowledge, PaginatedAlerts,
)
from app.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=PaginatedAlerts)
async def list_alerts(
    severity: AlertSeverity | None = Query(default=None),
    acknowledged: bool | None = Query(default=None),
    device_id: uuid.UUID | None = Query(default=None),
    fault_type: FaultType | None = Query(default=None),
    feedback: AlertFeedback | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedAlerts:
    return await AlertService(db).list_alerts(
        current_user, severity, acknowledged, page, page_size, device_id, fault_type, feedback, start, end
    )


@router.post("/acknowledge", response_model=dict)
async def bulk_acknowledge(
    data: BulkAcknowledge,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(require_operator),
) -> dict:
    count = await AlertService(db, redis).bulk_acknowledge(data.alert_ids, current_user)
    return {"acknowledged": count}


@router.get("/{alert_id}", response_model=AlertDetail)
async def get_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AlertDetail:
    return await AlertService(db).get_detail(alert_id, current_user)


@router.patch("/{alert_id}/acknowledge", response_model=AlertRead)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    # Body(default_factory=...) makes the request body optional — clients can send an
    # empty body or omit it entirely. Send {"acknowledged": false} to reopen an alert.
    body: AlertAcknowledge = Body(default_factory=AlertAcknowledge),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(require_operator),
) -> AlertRead:
    alert = await AlertService(db, redis).acknowledge(alert_id, current_user, body.acknowledged)
    return AlertRead.model_validate(alert)


@router.post("/{alert_id}/feedback", response_model=AlertRead)
async def alert_feedback(
    alert_id: uuid.UUID,
    data: AlertFeedbackIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_operator),
) -> AlertRead:
    """Label an alert as a true or false positive — feeds the detector precision metrics."""
    alert = await AlertService(db).set_feedback(alert_id, data.feedback, data.notes, current_user)
    return AlertRead.model_validate(alert)
