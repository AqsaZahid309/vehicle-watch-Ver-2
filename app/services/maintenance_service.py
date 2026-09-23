import math
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.core.utils import as_utc, utcnow
from app.models.alert import Alert, AlertFeedback
from app.models.device import Device
from app.models.maintenance import ServiceSchedule, WorkOrder, WorkOrderStatus
from app.models.user import User
from app.schemas.maintenance import (
    PaginatedWorkOrders, ServiceScheduleCreate, ServiceScheduleRead, ServiceScheduleUpdate,
    WorkOrderCreate, WorkOrderRead, WorkOrderUpdate,
)
from app.services.access import get_org_device, org_device_ids
from app.services.audit_service import record_audit

_OPEN_STATES = {WorkOrderStatus.OPEN.value, WorkOrderStatus.IN_PROGRESS.value, WorkOrderStatus.ON_HOLD.value}

# Faults a work order can confirm. NO_FAULT_FOUND ⇒ the alert was a false positive.
_CONFIRMING_CAUSES = {
    "COOLANT_LEAK", "BATTERY_FAILURE", "TRANSMISSION_STRESS", "BRAKE_WEAR", "ENGINE_STRESS",
    "WHEEL_BEARING", "LOW_OIL_PRESSURE", "TIRE_PRESSURE", "OTHER_FAULT",
}


def _default_title(alert: Alert, device: Device) -> str:
    fault = (alert.fault_type.value if alert.fault_type else "ANOMALY").replace("_", " ").title()
    return f"{fault} — {device.name}"


async def next_work_order_number(db: AsyncSession, org_id: uuid.UUID) -> int:
    current = (
        await db.execute(select(func.max(WorkOrder.number)).where(WorkOrder.organization_id == org_id))
    ).scalar_one_or_none()
    return (current or 0) + 1


def schedule_status(s: ServiceSchedule, device: Device) -> tuple[float | None, float | None, bool]:
    km_remaining = days_remaining = None
    due = False
    if s.interval_km:
        km_remaining = round(s.last_service_km + s.interval_km - (device.odometer_km or 0.0), 1)
        due = due or km_remaining <= 0
    if s.interval_days:
        next_at = as_utc(s.last_service_at) + timedelta(days=s.interval_days)
        days_remaining = round((next_at - utcnow()).total_seconds() / 86400.0, 1)
        due = due or days_remaining <= 0
    return km_remaining, days_remaining, due


class MaintenanceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Work orders ──────────────────────────────────────────────────────────

    async def _to_read(self, wo: WorkOrder) -> WorkOrderRead:
        read = WorkOrderRead.model_validate(wo)
        read.device_name = wo.device.name if wo.device else None
        read.total_cost = round((wo.parts_cost or 0) + (wo.labor_cost or 0), 2)
        if wo.assigned_to_id:
            user = await self._db.get(User, wo.assigned_to_id)
            read.assigned_to_email = user.email if user else None
        return read

    async def _get(self, wo_id: uuid.UUID, requester: User) -> WorkOrder:
        wo = (
            await self._db.execute(
                select(WorkOrder).where(
                    WorkOrder.id == wo_id, WorkOrder.organization_id == requester.organization_id
                )
            )
        ).scalars().first()
        if not wo:
            raise NotFoundError("Work order", str(wo_id))
        return wo

    async def _check_assignee(self, user_id: uuid.UUID, requester: User) -> None:
        found = (
            await self._db.execute(
                select(User.id).where(User.id == user_id, User.organization_id == requester.organization_id)
            )
        ).scalar_one_or_none()
        if not found:
            raise NotFoundError("User (assigned_to_id)", str(user_id))

    async def list_work_orders(
        self,
        requester: User,
        status: str | None = None,
        device_id: uuid.UUID | None = None,
        assigned_to_me: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedWorkOrders:
        conditions = [WorkOrder.organization_id == requester.organization_id]
        if status == "OPEN_ANY":
            conditions.append(WorkOrder.status.in_(_OPEN_STATES))
        elif status:
            conditions.append(WorkOrder.status == status)
        if device_id:
            conditions.append(WorkOrder.device_id == device_id)
        if assigned_to_me:
            conditions.append(WorkOrder.assigned_to_id == requester.id)

        total = (await self._db.execute(select(func.count(WorkOrder.id)).where(*conditions))).scalar_one()
        rows = (
            await self._db.execute(
                select(WorkOrder).where(*conditions)
                .order_by(WorkOrder.created_at.desc())
                .offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().unique().all()
        return PaginatedWorkOrders(
            items=[await self._to_read(w) for w in rows],
            total=total, page=page, page_size=page_size, pages=max(1, math.ceil(total / page_size)),
        )

    async def get(self, wo_id: uuid.UUID, requester: User) -> WorkOrderRead:
        return await self._to_read(await self._get(wo_id, requester))

    async def create(self, data: WorkOrderCreate, requester: User) -> WorkOrder:
        alert: Alert | None = None
        if data.alert_id:
            alert = (
                await self._db.execute(
                    select(Alert).where(Alert.id == data.alert_id, Alert.device_id.in_(org_device_ids(requester)))
                )
            ).scalar_one_or_none()
            if not alert:
                raise NotFoundError("Alert", str(data.alert_id))
            if data.device_id and data.device_id != alert.device_id:
                raise AppError("device_id does not match the alert's device", 400)

        device = await get_org_device(self._db, data.device_id or alert.device_id, requester)
        if data.assigned_to_id:
            await self._check_assignee(data.assigned_to_id, requester)

        title = data.title or (_default_title(alert, device) if alert else f"Maintenance — {device.name}")
        description = data.description
        if not description and alert is not None and alert.llm_summary:
            description = alert.llm_summary

        wo = WorkOrder(
            organization_id=requester.organization_id,
            number=await next_work_order_number(self._db, requester.organization_id),
            device_id=device.id,
            alert_id=alert.id if alert else None,
            title=title,
            description=description,
            priority=data.priority.value,
            assigned_to_id=data.assigned_to_id,
            created_by_id=requester.id,
            due_date=data.due_date,
        )
        self._db.add(wo)
        # Raising a work order counts as acknowledging the alert.
        if alert is not None and not alert.acknowledged:
            alert.acknowledged = True
            alert.acknowledged_at = utcnow()
            alert.acknowledged_by_id = requester.id
        await self._db.flush()
        await self._db.refresh(wo)
        record_audit(self._db, requester, "work_order.created", "work_order", wo.id,
                     {"number": wo.number, "device": device.name})
        return wo

    async def update(self, wo_id: uuid.UUID, data: WorkOrderUpdate, requester: User) -> WorkOrder:
        wo = await self._get(wo_id, requester)
        changes = data.model_dump(exclude_unset=True, exclude={"unassign"})
        if changes.get("assigned_to_id"):
            await self._check_assignee(changes["assigned_to_id"], requester)

        new_status = changes.get("status")
        if new_status is not None:
            new_status = new_status.value if hasattr(new_status, "value") else new_status
            if new_status == WorkOrderStatus.RESOLVED.value:
                root_cause = changes.get("root_cause") or wo.root_cause
                if not root_cause:
                    raise AppError("Set a root_cause before resolving the work order", 400)

        for field, value in changes.items():
            setattr(wo, field, value.value if hasattr(value, "value") else value)
        if data.unassign:
            wo.assigned_to_id = None

        now = utcnow()
        if new_status == WorkOrderStatus.IN_PROGRESS.value and wo.started_at is None:
            wo.started_at = now
        if new_status == WorkOrderStatus.RESOLVED.value:
            wo.resolved_at = now
            await self._on_resolved(wo, requester)
        elif new_status in _OPEN_STATES:
            wo.resolved_at = None

        wo.updated_at = now
        await self._db.flush()
        await self._db.refresh(wo)
        record_audit(self._db, requester, "work_order.updated", "work_order", wo.id,
                     {k: str(v.value if hasattr(v, "value") else v) for k, v in changes.items()})
        return wo

    async def _on_resolved(self, wo: WorkOrder, requester: User) -> None:
        """Close the loop: label the source alert and reset the service schedule."""
        if wo.alert_id:
            alert = await self._db.get(Alert, wo.alert_id)
            if alert is not None and alert.feedback is None:
                if wo.root_cause == "NO_FAULT_FOUND":
                    alert.feedback = AlertFeedback.FALSE_POSITIVE.value
                elif wo.root_cause in _CONFIRMING_CAUSES:
                    alert.feedback = AlertFeedback.TRUE_POSITIVE.value
                if alert.feedback:
                    alert.feedback_by_id = requester.id
                    alert.feedback_notes = f"Set from work order #{wo.number} (root cause {wo.root_cause})"
        if wo.schedule_id:
            schedule = await self._db.get(ServiceSchedule, wo.schedule_id)
            device = await self._db.get(Device, wo.device_id)
            if schedule is not None and device is not None:
                schedule.last_service_at = utcnow()
                schedule.last_service_km = device.odometer_km or 0.0

    async def delete(self, wo_id: uuid.UUID, requester: User) -> None:
        wo = await self._get(wo_id, requester)
        record_audit(self._db, requester, "work_order.deleted", "work_order", wo.id, {"number": wo.number})
        await self._db.delete(wo)

    # ── Service schedules ────────────────────────────────────────────────────

    async def _schedule_read(self, s: ServiceSchedule, device: Device) -> ServiceScheduleRead:
        read = ServiceScheduleRead.model_validate(s)
        read.device_name = device.name
        read.km_remaining, read.days_remaining, read.due = schedule_status(s, device)
        return read

    async def list_schedules(self, requester: User, device_id: uuid.UUID | None = None) -> list[ServiceScheduleRead]:
        stmt = (
            select(ServiceSchedule, Device)
            .join(Device, Device.id == ServiceSchedule.device_id)
            .where(Device.organization_id == requester.organization_id)
        )
        if device_id:
            stmt = stmt.where(ServiceSchedule.device_id == device_id)
        rows = (await self._db.execute(stmt.order_by(Device.name, ServiceSchedule.name))).unique().all()
        return [await self._schedule_read(s, d) for s, d in rows]

    async def _get_schedule(self, schedule_id: uuid.UUID, requester: User) -> tuple[ServiceSchedule, Device]:
        row = (
            await self._db.execute(
                select(ServiceSchedule, Device)
                .join(Device, Device.id == ServiceSchedule.device_id)
                .where(ServiceSchedule.id == schedule_id, Device.organization_id == requester.organization_id)
            )
        ).unique().first()
        if not row:
            raise NotFoundError("Service schedule", str(schedule_id))
        return row[0], row[1]

    async def create_schedule(self, data: ServiceScheduleCreate, requester: User) -> ServiceScheduleRead:
        device = await get_org_device(self._db, data.device_id, requester)
        s = ServiceSchedule(
            device_id=device.id,
            name=data.name,
            interval_km=data.interval_km,
            interval_days=data.interval_days,
            last_service_at=data.last_service_at or utcnow(),
            last_service_km=data.last_service_km if data.last_service_km is not None else (device.odometer_km or 0.0),
        )
        self._db.add(s)
        await self._db.flush()
        await self._db.refresh(s)
        record_audit(self._db, requester, "schedule.created", "service_schedule", s.id, {"name": s.name})
        return await self._schedule_read(s, device)

    async def update_schedule(
        self, schedule_id: uuid.UUID, data: ServiceScheduleUpdate, requester: User
    ) -> ServiceScheduleRead:
        s, device = await self._get_schedule(schedule_id, requester)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(s, field, value)
        await self._db.flush()
        return await self._schedule_read(s, device)

    async def delete_schedule(self, schedule_id: uuid.UUID, requester: User) -> None:
        s, _ = await self._get_schedule(schedule_id, requester)
        await self._db.delete(s)
