import json
import uuid
from datetime import timedelta

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.security import generate_device_api_key
from app.core.utils import as_utc, utcnow
from app.models.alert import Alert, AlertSeverity
from app.models.device import Device
from app.models.fleet import Driver
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceStatusResponse, DeviceUpdate, LiveDevice
from app.services.access import get_org_device
from app.services.audit_service import record_audit

ONLINE_WINDOW = timedelta(minutes=2)
_SEVERITY_PENALTY = {AlertSeverity.CRITICAL: 25, AlertSeverity.MEDIUM: 10, AlertSeverity.LOW: 3}


def latest_cache_key(device_id: uuid.UUID | str) -> str:
    return f"device:{device_id}:latest"


class DeviceService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis) -> None:
        self._db = db
        self._redis = redis

    async def _check_same_org_user(self, user_id: uuid.UUID, requester: User) -> None:
        result = await self._db.execute(
            select(User.id).where(User.id == user_id, User.organization_id == requester.organization_id)
        )
        if not result.scalar_one_or_none():
            raise NotFoundError("User (owner_id)", str(user_id))

    async def _check_same_org_driver(self, driver_id: uuid.UUID, requester: User) -> None:
        result = await self._db.execute(
            select(Driver.id).where(Driver.id == driver_id, Driver.organization_id == requester.organization_id)
        )
        if not result.scalar_one_or_none():
            raise NotFoundError("Driver", str(driver_id))

    async def create(self, data: DeviceCreate, requester: User) -> Device:
        owner_id = data.owner_id or requester.id
        if owner_id != requester.id:
            # Validate before INSERT — a bad FK would otherwise surface as a generic 500.
            await self._check_same_org_user(owner_id, requester)

        device = Device(
            organization_id=requester.organization_id,
            owner_id=owner_id,
            **data.model_dump(exclude={"owner_id"}),
        )
        self._db.add(device)
        await self._db.flush()
        await self._db.refresh(device)
        record_audit(self._db, requester, "device.created", "device", device.id, {"name": device.name})
        return device

    async def list_devices(self, requester: User) -> list[Device]:
        result = await self._db.execute(
            select(Device)
            .where(Device.organization_id == requester.organization_id)
            .order_by(Device.name)
        )
        return list(result.scalars().all())

    async def get_by_id(self, device_id: uuid.UUID, requester: User) -> Device:
        return await get_org_device(self._db, device_id, requester)

    async def update(self, device_id: uuid.UUID, data: DeviceUpdate, requester: User) -> Device:
        device = await get_org_device(self._db, device_id, requester)
        changes = data.model_dump(exclude_unset=True, exclude={"unassign_driver"})
        if changes.get("assigned_driver_id"):
            await self._check_same_org_driver(changes["assigned_driver_id"], requester)
        for field, value in changes.items():
            setattr(device, field, value)
        if data.unassign_driver:
            device.assigned_driver_id = None
        await self._db.flush()
        await self._db.refresh(device)
        record_audit(self._db, requester, "device.updated", "device", device.id,
                     {k: str(v) for k, v in changes.items()})
        return device

    async def rotate_api_key(self, device_id: uuid.UUID, requester: User) -> tuple[Device, str]:
        device = await get_org_device(self._db, device_id, requester)
        full_key, prefix, key_hash = generate_device_api_key()
        device.api_key_prefix = prefix
        device.api_key_hash = key_hash
        await self._db.flush()
        record_audit(self._db, requester, "device.api_key_rotated", "device", device.id, {"prefix": prefix})
        return device, full_key

    async def revoke_api_key(self, device_id: uuid.UUID, requester: User) -> None:
        device = await get_org_device(self._db, device_id, requester)
        device.api_key_prefix = None
        device.api_key_hash = None
        record_audit(self._db, requester, "device.api_key_revoked", "device", device.id)
        await self._db.flush()

    async def get_status(self, device_id: uuid.UUID, requester: User) -> DeviceStatusResponse:
        await get_org_device(self._db, device_id, requester)
        cached = await self._redis.get(latest_cache_key(device_id))
        if cached:
            return DeviceStatusResponse(device_id=device_id, cached=True, latest_telemetry=json.loads(cached))
        return DeviceStatusResponse(device_id=device_id, cached=False, latest_telemetry=None)

    async def delete(self, device_id: uuid.UUID, requester: User) -> None:
        device = await get_org_device(self._db, device_id, requester)
        record_audit(self._db, requester, "device.deleted", "device", device.id, {"name": device.name})
        await self._db.delete(device)

    async def live(self, requester: User) -> list[LiveDevice]:
        """Latest position + health for every vehicle — powers the live map and fleet grid."""
        devices = await self.list_devices(requester)
        if not devices:
            return []

        cached_values: list[str | None] = []
        try:
            cached_values = await self._redis.mget([latest_cache_key(d.id) for d in devices])
        except Exception:
            cached_values = [None] * len(devices)

        since = utcnow() - timedelta(days=7)
        alert_rows = await self._db.execute(
            select(Alert.device_id, Alert.severity, func.count(Alert.id))
            .where(
                Alert.device_id.in_([d.id for d in devices]),
                Alert.acknowledged.is_(False),
                Alert.created_at >= since,
            )
            .group_by(Alert.device_id, Alert.severity)
        )
        open_by_device: dict[uuid.UUID, int] = {}
        penalty: dict[uuid.UUID, int] = {}
        for device_id, severity, count in alert_rows:
            open_by_device[device_id] = open_by_device.get(device_id, 0) + count
            penalty[device_id] = penalty.get(device_id, 0) + _SEVERITY_PENALTY[severity] * count

        now = utcnow()
        out: list[LiveDevice] = []
        for device, raw in zip(devices, cached_values or [None] * len(devices)):
            latest = json.loads(raw) if raw else None
            last_seen = as_utc(device.last_seen_at)
            out.append(
                LiveDevice(
                    id=device.id,
                    name=device.name,
                    device_type=device.device_type,
                    license_plate=device.license_plate,
                    is_active=device.is_active,
                    online=bool(last_seen and now - last_seen < ONLINE_WINDOW),
                    last_seen_at=last_seen,
                    latest=latest,
                    open_alerts=open_by_device.get(device.id, 0),
                    health_score=max(0, 100 - penalty.get(device.id, 0)),
                    driver_name=device.assigned_driver.name if device.assigned_driver else None,
                )
            )
        return out
