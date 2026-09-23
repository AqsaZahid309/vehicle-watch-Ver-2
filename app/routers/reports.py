import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.utils import utcnow
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["Reports"])

_EXPORTS = ("alerts", "trips", "work_orders", "fuel_events", "telemetry", "audit")


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or utcnow()
    start = start or end - timedelta(days=30)
    if start >= end:
        raise AppError("start must be before end", 400)
    if end - start > timedelta(days=366):
        raise AppError("Report range is limited to one year", 400)
    return start, end


@router.get("/summary")
async def report_summary(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Fleet KPIs, alert quality, maintenance cost/downtime and estimated cost avoided for a period."""
    s, e = _range(start, end)
    return await ReportService(db).summary(user.organization_id, s, e)


@router.get("/export/{kind}")
async def export_csv(
    kind: str,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    device_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: User = Depends(get_current_user),
) -> Response:
    if kind not in _EXPORTS:
        raise AppError(f"Unknown export '{kind}'. Choose one of: {', '.join(_EXPORTS)}", 404)
    if kind == "audit" and user.role.value != "ADMIN":
        raise AppError("Only admins can export the audit log", 403)
    s, e = _range(start, end)
    try:
        body = await ReportService(db).export_csv(kind, user, s, e, device_id)
    except ValueError as exc:
        raise AppError(str(exc), 400)
    filename = f"vehiclewatch-{kind}-{s:%Y%m%d}-{e:%Y%m%d}.csv"
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
