import math
import uuid
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import NotFoundError
from app.core.utils import utcnow
from app.models.alert import Alert, AlertFeedback, AlertSeverity, FaultType
from app.models.device import Device
from app.models.maintenance import WorkOrder
from app.models.telemetry import Telemetry
from app.models.user import User
from app.schemas.alert import AlertDetail, AlertRead, PaginatedAlerts
from app.schemas.telemetry import TelemetryRead
from app.services.access import org_device_ids
from app.services.audit_service import record_audit


class AlertService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis | None = None) -> None:
        self._db = db
        self._redis = redis

    async def _work_orders_for(self, alert_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
        if not alert_ids:
            return {}
        rows = await self._db.execute(
            select(WorkOrder.alert_id, WorkOrder.id)
            .where(WorkOrder.alert_id.in_(alert_ids))
            .order_by(WorkOrder.created_at)
        )
        return {alert_id: wo_id for alert_id, wo_id in rows}

    async def _to_reads(self, rows: list[tuple[Alert, str]]) -> list[AlertRead]:
        wo_map = await self._work_orders_for([a.id for a, _ in rows])
        out = []
        for alert, device_name in rows:
            item = AlertRead.model_validate(alert)
            item.device_name = device_name
            item.work_order_id = wo_map.get(alert.id)
            out.append(item)
        return out

    async def list_alerts(
        self,
        requester: User,
        severity: AlertSeverity | None = None,
        acknowledged: bool | None = None,
        page: int = 1,
        page_size: int = 50,
        device_id: uuid.UUID | None = None,
        fault_type: FaultType | None = None,
        feedback: AlertFeedback | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> PaginatedAlerts:
        conditions = [Alert.device_id.in_(org_device_ids(requester))]
        if severity:
            conditions.append(Alert.severity == severity)
        if acknowledged is not None:
            conditions.append(Alert.acknowledged == acknowledged)
        if device_id:
            conditions.append(Alert.device_id == device_id)
        if fault_type:
            conditions.append(Alert.fault_type == fault_type)
        if feedback:
            conditions.append(Alert.feedback == feedback.value)
        if start:
            conditions.append(Alert.created_at >= start)
        if end:
            conditions.append(Alert.created_at <= end)

        total = (await self._db.execute(select(func.count(Alert.id)).where(*conditions))).scalar_one()

        result = await self._db.execute(
            select(Alert, Device.name)
            .join(Device, Device.id == Alert.device_id)
            .where(*conditions)
            .order_by(Alert.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = await self._to_reads([(a, n) for a, n in result.all()])

        return PaginatedAlerts(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=max(1, math.ceil(total / page_size)),
        )

    async def get_by_id(self, alert_id: uuid.UUID, requester: User) -> Alert:
        result = await self._db.execute(
            select(Alert).where(
                Alert.id == alert_id, Alert.device_id.in_(org_device_ids(requester))
            )
        )
        alert = result.scalar_one_or_none()
        if not alert:
            raise NotFoundError("Alert", str(alert_id))
        return alert

    async def get_detail(self, alert_id: uuid.UUID, requester: User) -> AlertDetail:
        alert = await self.get_by_id(alert_id, requester)
        device = await self._db.get(Device, alert.device_id)
        [read] = await self._to_reads([(alert, device.name if device else None)])
        detail = AlertDetail(**read.model_dump())
        if alert.telemetry_id:
            telemetry = await self._db.get(Telemetry, alert.telemetry_id)
            if telemetry:
                detail.telemetry = TelemetryRead.model_validate(telemetry).model_dump(mode="json")
        return detail

    async def acknowledge(self, alert_id: uuid.UUID, requester: User, acknowledged: bool = True) -> Alert:
        alert = await self.get_by_id(alert_id, requester)
        alert.acknowledged = acknowledged
        alert.acknowledged_at = utcnow() if acknowledged else None
        alert.acknowledged_by_id = requester.id if acknowledged else None
        record_audit(self._db, requester, "alert.acknowledged" if acknowledged else "alert.reopened",
                     "alert", alert.id)
        await self._db.flush()
        await self._db.refresh(alert)
        await events.publish(self._redis, requester.organization_id, "alert_updated",
                             {"id": str(alert.id), "acknowledged": acknowledged})
        return alert

    async def bulk_acknowledge(self, alert_ids: list[uuid.UUID], requester: User) -> int:
        result = await self._db.execute(
            update(Alert)
            .where(
                Alert.id.in_(alert_ids),
                Alert.device_id.in_(org_device_ids(requester)),
                Alert.acknowledged.is_(False),
            )
            .values(acknowledged=True, acknowledged_at=utcnow(), acknowledged_by_id=requester.id)
            .execution_options(synchronize_session=False)
        )
        record_audit(self._db, requester, "alert.bulk_acknowledged", "alert", None,
                     {"count": result.rowcount})
        await events.publish(self._redis, requester.organization_id, "alert_updated", {"bulk": True})
        return result.rowcount or 0

    async def set_feedback(
        self, alert_id: uuid.UUID, feedback: AlertFeedback, notes: str | None, requester: User
    ) -> Alert:
        alert = await self.get_by_id(alert_id, requester)
        alert.feedback = feedback.value
        alert.feedback_notes = notes
        alert.feedback_by_id = requester.id
        # Labelling an alert implies someone has looked at it.
        if not alert.acknowledged:
            alert.acknowledged = True
            alert.acknowledged_at = utcnow()
            alert.acknowledged_by_id = requester.id
        record_audit(self._db, requester, "alert.feedback", "alert", alert.id, {"feedback": feedback.value})
        await self._db.flush()
        await self._db.refresh(alert)
        return alert
