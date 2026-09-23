import uuid

from fastapi import APIRouter, Depends, Query, Body, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_operator
from app.models.alert import AlertSeverity
from app.models.user import User
from app.schemas.alert import AlertRead, AlertAcknowledge, PaginatedAlerts
from app.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=PaginatedAlerts)
async def list_alerts(
    severity: AlertSeverity | None = Query(default=None),
    acknowledged: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedAlerts:
    service = AlertService(db)
    return await service.list_alerts(current_user, severity, acknowledged, page, page_size)


@router.get("/{alert_id}", response_model=AlertRead)
async def get_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AlertRead:
    service = AlertService(db)
    alert = await service.get_by_id(alert_id, current_user)
    return AlertRead.model_validate(alert)


@router.patch("/{alert_id}/acknowledge", response_model=AlertRead)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    # Body(default=...) makes the request body optional — clients can send an empty
    # body or omit it entirely. Using a plain default `= AlertAcknowledge()` is not
    # recognized by FastAPI as an optional body and causes 422 in some versions.
    _body: AlertAcknowledge = Body(default_factory=AlertAcknowledge),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_operator),
) -> AlertRead:
    service = AlertService(db)
    alert = await service.acknowledge(alert_id, current_user)
    return AlertRead.model_validate(alert)
