import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeviceCreate(BaseModel):
    name: str
    device_type: str
    owner_id: uuid.UUID


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    device_type: str
    owner_id: uuid.UUID
    is_active: bool
    registered_at: datetime


class DeviceStatusResponse(BaseModel):
    device_id: uuid.UUID
    cached: bool
    latest_telemetry: dict | None = None
