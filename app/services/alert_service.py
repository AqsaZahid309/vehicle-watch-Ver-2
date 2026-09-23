import math
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ForbiddenError
from app.models.alert import Alert, AlertSeverity
from app.models.device import Device
from app.models.user import User, UserRole
from app.schemas.alert import AlertRead, PaginatedAlerts


class AlertService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_alerts(
        self,
        requester: User,
        severity: AlertSeverity | None = None,
        acknowledged: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedAlerts:
        stmt = select(Alert)

        # Operators only see alerts for their own devices
        if requester.role != UserRole.ADMIN:
            owned_device_ids = (
                select(Device.id).where(Device.owner_id == requester.id)
            )
            stmt = stmt.where(Alert.device_id.in_(owned_device_ids))

        if severity:
            stmt = stmt.where(Alert.severity == severity)
        if acknowledged is not None:
            stmt = stmt.where(Alert.acknowledged == acknowledged)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        result = await self._db.execute(
            stmt.order_by(Alert.created_at.desc()).offset(offset).limit(page_size)
        )
        items = list(result.scalars().all())

        return PaginatedAlerts(
            items=[AlertRead.model_validate(a) for a in items],
            total=total,
            page=page,
            page_size=page_size,
            pages=max(1, math.ceil(total / page_size)),
        )

    async def get_by_id(self, alert_id: uuid.UUID, requester: User) -> Alert:
        result = await self._db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        if not alert:
            raise NotFoundError("Alert", str(alert_id))

        if requester.role != UserRole.ADMIN:
            device_result = await self._db.execute(
                select(Device).where(Device.id == alert.device_id)
            )
            device = device_result.scalar_one_or_none()
            if not device or device.owner_id != requester.id:
                raise ForbiddenError("You do not have access to this alert")

        return alert

    async def acknowledge(self, alert_id: uuid.UUID, requester: User) -> Alert:
        alert = await self.get_by_id(alert_id, requester)
        alert.acknowledged = True
        await self._db.flush()
        await self._db.refresh(alert)
        return alert
