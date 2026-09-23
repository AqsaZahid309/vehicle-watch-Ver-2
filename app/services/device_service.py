import json
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ForbiddenError
from app.models.device import Device
from app.models.user import User, UserRole
from app.schemas.device import DeviceCreate, DeviceStatusResponse


class DeviceService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis) -> None:
        self._db = db
        self._redis = redis

    async def create(self, data: DeviceCreate, requester: User) -> Device:
        # Validate that the target owner exists before attempting the INSERT.
        # Without this check a bad owner_id produces an unhandled FK IntegrityError
        # which surfaces as a generic 500 instead of a meaningful 404.
        from app.models.user import User as UserModel
        owner_result = await self._db.execute(
            select(UserModel).where(UserModel.id == data.owner_id)
        )
        if not owner_result.scalar_one_or_none():
            raise NotFoundError("User (owner_id)", str(data.owner_id))

        device = Device(
            name=data.name,
            device_type=data.device_type,
            owner_id=data.owner_id,
        )
        self._db.add(device)
        await self._db.flush()
        await self._db.refresh(device)
        return device

    async def list_devices(self, requester: User) -> list[Device]:
        stmt = select(Device)
        if requester.role != UserRole.ADMIN:
            stmt = stmt.where(Device.owner_id == requester.id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, device_id: uuid.UUID, requester: User) -> Device:
        result = await self._db.execute(
            select(Device).where(Device.id == device_id)
        )
        device = result.scalar_one_or_none()
        if not device:
            raise NotFoundError("Device", str(device_id))

        if requester.role != UserRole.ADMIN and device.owner_id != requester.id:
            raise ForbiddenError("You do not have access to this device")

        return device

    async def get_status(self, device_id: uuid.UUID, requester: User) -> DeviceStatusResponse:
        await self.get_by_id(device_id, requester)

        cache_key = f"device:{device_id}:latest"
        cached = await self._redis.get(cache_key)

        if cached:
            return DeviceStatusResponse(
                device_id=device_id,
                cached=True,
                latest_telemetry=json.loads(cached),
            )
        return DeviceStatusResponse(device_id=device_id, cached=False, latest_telemetry=None)

    async def delete(self, device_id: uuid.UUID) -> None:
        result = await self._db.execute(
            select(Device).where(Device.id == device_id)
        )
        device = result.scalar_one_or_none()
        if not device:
            raise NotFoundError("Device", str(device_id))
        await self._db.delete(device)
