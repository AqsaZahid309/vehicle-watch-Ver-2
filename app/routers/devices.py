import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin, require_manager
from app.models.user import User
from app.redis import get_redis
from app.schemas.device import (
    DeviceApiKeyResponse, DeviceCreate, DeviceRead, DeviceStatusResponse, DeviceUpdate, LiveDevice,
)
from app.services.device_service import DeviceService

router = APIRouter(prefix="/devices", tags=["Devices"])


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
async def create_device(
    data: DeviceCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    manager: User = Depends(require_manager),
) -> DeviceRead:
    device = await DeviceService(db, redis).create(data, manager)
    return DeviceRead.model_validate(device)


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> list[DeviceRead]:
    devices = await DeviceService(db, redis).list_devices(current_user)
    return [DeviceRead.model_validate(d) for d in devices]


@router.get("/live", response_model=list[LiveDevice])
async def live_devices(
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> list[LiveDevice]:
    """Latest position, online state and health score for every vehicle."""
    return await DeviceService(db, redis).live(current_user)


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> DeviceRead:
    device = await DeviceService(db, redis).get_by_id(device_id, current_user)
    return DeviceRead.model_validate(device)


@router.patch("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: uuid.UUID,
    data: DeviceUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    manager: User = Depends(require_manager),
) -> DeviceRead:
    device = await DeviceService(db, redis).update(device_id, data, manager)
    return DeviceRead.model_validate(device)


@router.get("/{device_id}/status", response_model=DeviceStatusResponse)
async def get_device_status(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> DeviceStatusResponse:
    return await DeviceService(db, redis).get_status(device_id, current_user)


@router.post("/{device_id}/api-key", response_model=DeviceApiKeyResponse)
async def rotate_api_key(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    manager: User = Depends(require_manager),
) -> DeviceApiKeyResponse:
    """
    Issue (or rotate) the device's ingestion key. The previous key stops working
    immediately. The key is shown once and cannot be retrieved later.
    """
    device, key = await DeviceService(db, redis).rotate_api_key(device_id, manager)
    return DeviceApiKeyResponse(device_id=device.id, api_key=key, api_key_prefix=device.api_key_prefix)


@router.delete("/{device_id}/api-key", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    manager: User = Depends(require_manager),
) -> None:
    await DeviceService(db, redis).revoke_api_key(device_id, manager)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
) -> None:
    await DeviceService(db, redis).delete(device_id, admin)
