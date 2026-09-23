import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.notification import Notification, NotificationChannel, NotificationDelivery
from app.models.user import User
from app.schemas.notification import (
    ChannelCreate, ChannelRead, ChannelUpdate, DeliveryRead, NotificationList, NotificationRead,
)
from app.services.audit_service import record_audit
from app.services.notification_service import send_via_channel

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: User = Depends(get_current_user),
) -> NotificationList:
    base = [Notification.organization_id == user.organization_id]
    stmt = select(Notification).where(*base)
    if unread_only:
        stmt = stmt.where(Notification.read.is_(False))
    items = (await db.execute(stmt.order_by(Notification.created_at.desc()).limit(limit))).scalars().all()
    unread = (
        await db.execute(select(func.count(Notification.id)).where(*base, Notification.read.is_(False)))
    ).scalar_one()
    return NotificationList(items=[NotificationRead.model_validate(n) for n in items], unread=unread)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(get_current_user)) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.organization_id == user.organization_id, Notification.read.is_(False))
        .values(read=True)
        .execution_options(synchronize_session=False)
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(get_current_user)
) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.organization_id == user.organization_id)
        .values(read=True)
        .execution_options(synchronize_session=False)
    )


# ── Channels ──────────────────────────────────────────────────────────────────

async def _channel(db: AsyncSession, channel_id: uuid.UUID, user: User) -> NotificationChannel:
    ch = (
        await db.execute(
            select(NotificationChannel).where(
                NotificationChannel.id == channel_id, NotificationChannel.organization_id == user.organization_id
            )
        )
    ).scalar_one_or_none()
    if not ch:
        raise NotFoundError("Notification channel", str(channel_id))
    return ch


@router.get("/channels", response_model=list[ChannelRead])
async def list_channels(db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(get_current_user)):
    rows = (
        await db.execute(
            select(NotificationChannel)
            .where(NotificationChannel.organization_id == user.organization_id)
            .order_by(NotificationChannel.created_at)
        )
    ).scalars().all()
    return [ChannelRead.model_validate(c) for c in rows]


@router.post("/channels", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
async def create_channel(data: ChannelCreate, db: AsyncSession = Depends(get_db, scope="function"), admin: User = Depends(require_admin)):
    ch = NotificationChannel(
        organization_id=admin.organization_id,
        name=data.name,
        channel_type=data.channel_type.value,
        target=data.target,
        min_severity=data.min_severity.value,
        event_types=[e.value for e in data.event_types],
        enabled=data.enabled,
    )
    db.add(ch)
    await db.flush()
    await db.refresh(ch)
    record_audit(db, admin, "channel.created", "notification_channel", ch.id,
                 {"name": ch.name, "type": ch.channel_type})
    return ChannelRead.model_validate(ch)


@router.patch("/channels/{channel_id}", response_model=ChannelRead)
async def update_channel(
    channel_id: uuid.UUID, data: ChannelUpdate, db: AsyncSession = Depends(get_db, scope="function"), admin: User = Depends(require_admin)
):
    ch = await _channel(db, channel_id, admin)
    changes = data.model_dump(exclude_unset=True)
    if "target" in changes:
        # Re-run the type-specific target validation.
        ChannelCreate(name=ch.name, channel_type=ch.channel_type, target=changes["target"])
    if "min_severity" in changes and changes["min_severity"] is not None:
        changes["min_severity"] = changes["min_severity"].value
    if "event_types" in changes and changes["event_types"] is not None:
        changes["event_types"] = [e.value for e in changes["event_types"]]
    for k, v in changes.items():
        setattr(ch, k, v)
    await db.flush()
    await db.refresh(ch)
    return ChannelRead.model_validate(ch)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"), admin: User = Depends(require_admin)):
    ch = await _channel(db, channel_id, admin)
    record_audit(db, admin, "channel.deleted", "notification_channel", ch.id, {"name": ch.name})
    await db.delete(ch)


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"), admin: User = Depends(require_admin)):
    """Send a test message through the channel and report success or the delivery error."""
    ch = await _channel(db, channel_id, admin)
    probe = Notification(
        id=uuid.uuid4(), organization_id=admin.organization_id, event_type="TEST", severity="LOW",
        title="VehicleWatch test notification",
        body=f"This channel ('{ch.name}') is configured correctly.", link="/settings",
    )
    try:
        await send_via_channel(ch, probe)
    except Exception as exc:
        raise AppError(f"Delivery failed: {exc}", 502)
    return {"status": "sent"}


@router.get("/deliveries", response_model=list[DeliveryRead])
async def list_deliveries(
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db, scope="function"),
    admin: User = Depends(require_admin),
):
    rows = (
        await db.execute(
            select(NotificationDelivery)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(Notification.organization_id == admin.organization_id)
            .order_by(NotificationDelivery.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [DeliveryRead.model_validate(d) for d in rows]
