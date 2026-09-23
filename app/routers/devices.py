import uuid

from fastapi import APIRouter, Depends, status
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.user import User
from app.redis import get_redis
from app.schemas.device import DeviceCreate, DeviceRead, DeviceStatusResponse
from app.services.device_service import DeviceService

router = APIRouter(prefix="/devices", tags=["Devices"])


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
async def create_device(
    data: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _admin: User = Depends(require_admin),
) -> DeviceRead:
    service = DeviceService(db, redis)
    device = await service.create(data, _admin)
    return DeviceRead.model_validate(device)


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> list[DeviceRead]:
    service = DeviceService(db, redis)
    devices = await service.list_devices(current_user)
    return [DeviceRead.model_validate(d) for d in devices]


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> DeviceRead:
    service = DeviceService(db, redis)
    device = await service.get_by_id(device_id, current_user)
    return DeviceRead.model_validate(device)


@router.get("/{device_id}/status", response_model=DeviceStatusResponse)
async def get_device_status(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> DeviceStatusResponse:
    service = DeviceService(db, redis)
    return await service.get_status(device_id, current_user)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _admin: User = Depends(require_admin),
) -> None:
    service = DeviceService(db, redis)
    await service.delete(device_id)
