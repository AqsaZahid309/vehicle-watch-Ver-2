import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.database import get_db
from app.dependencies import get_current_user, require_manager, require_operator, require_technician
from app.models.notification import EventType
from app.models.user import User
from app.redis import get_redis
from app.schemas.maintenance import (
    PaginatedWorkOrders, ServiceScheduleCreate, ServiceScheduleRead, ServiceScheduleUpdate,
    WorkOrderCreate, WorkOrderRead, WorkOrderUpdate,
)
from app.services.maintenance_service import MaintenanceService
from app.services.notification_service import NotificationService, deliver_in_new_session

router = APIRouter(prefix="/maintenance", tags=["Maintenance"])


async def _notify_assignment(
    db: AsyncSession, redis, wo: WorkOrderRead, actor: User, background: BackgroundTasks
) -> None:
    if not wo.assigned_to_id or wo.assigned_to_id == actor.id:
        return
    notifier = NotificationService(db, redis)
    n = await notifier.create(
        actor.organization_id, EventType.WORK_ORDER.value,
        f"Work order #{wo.number} assigned to {wo.assigned_to_email}",
        f"{wo.title} ({wo.priority} priority) on {wo.device_name}.",
        severity=None, device_id=wo.device_id, link="/maintenance",
    )
    if await notifier.has_channels(actor.organization_id):
        background.add_task(deliver_in_new_session, [n.id])


@router.get("/work-orders", response_model=PaginatedWorkOrders)
async def list_work_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    device_id: uuid.UUID | None = Query(default=None),
    assigned_to_me: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> PaginatedWorkOrders:
    """status: OPEN | IN_PROGRESS | ON_HOLD | RESOLVED | CANCELLED | OPEN_ANY (all unresolved)."""
    return await MaintenanceService(db).list_work_orders(current_user, status_filter, device_id, assigned_to_me, page, page_size)


@router.post("/work-orders", response_model=WorkOrderRead, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    data: WorkOrderCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(require_operator),
) -> WorkOrderRead:
    """Raise a work order — from an alert (alert_id) or for a vehicle directly (device_id)."""
    svc = MaintenanceService(db)
    wo = await svc.create(data, current_user)
    read = await svc.get(wo.id, current_user)
    await _notify_assignment(db, redis, read, current_user, background)
    await events.publish(redis, current_user.organization_id, "work_order", {"id": str(wo.id), "action": "created"})
    return read


@router.get("/work-orders/{wo_id}", response_model=WorkOrderRead)
async def get_work_order(
    wo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> WorkOrderRead:
    return await MaintenanceService(db).get(wo_id, current_user)


@router.patch("/work-orders/{wo_id}", response_model=WorkOrderRead)
async def update_work_order(
    wo_id: uuid.UUID,
    data: WorkOrderUpdate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(require_technician),
) -> WorkOrderRead:
    """Resolving requires a root_cause; it labels the source alert TRUE/FALSE_POSITIVE automatically."""
    svc = MaintenanceService(db)
    await svc.update(wo_id, data, current_user)
    read = await svc.get(wo_id, current_user)
    if data.assigned_to_id:
        await _notify_assignment(db, redis, read, current_user, background)
    await events.publish(redis, current_user.organization_id, "work_order", {"id": str(wo_id), "action": "updated"})
    return read


@router.delete("/work-orders/{wo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_order(
    wo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_manager),
) -> None:
    await MaintenanceService(db).delete(wo_id, current_user)


@router.get("/schedules", response_model=list[ServiceScheduleRead])
async def list_schedules(
    device_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
) -> list[ServiceScheduleRead]:
    return await MaintenanceService(db).list_schedules(current_user, device_id)


@router.post("/schedules", response_model=ServiceScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    data: ServiceScheduleCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_manager),
) -> ServiceScheduleRead:
    return await MaintenanceService(db).create_schedule(data, current_user)


@router.patch("/schedules/{schedule_id}", response_model=ServiceScheduleRead)
async def update_schedule(
    schedule_id: uuid.UUID,
    data: ServiceScheduleUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_manager),
) -> ServiceScheduleRead:
    return await MaintenanceService(db).update_schedule(schedule_id, data, current_user)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_manager),
) -> None:
    await MaintenanceService(db).delete_schedule(schedule_id, current_user)
