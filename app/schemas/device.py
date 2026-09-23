import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    device_type: str = Field(min_length=1, max_length=100)
    # Optional: defaults to the creating user. Must belong to the same organization.
    owner_id: uuid.UUID | None = None
    vin: str | None = Field(default=None, max_length=32)
    license_plate: str | None = Field(default=None, max_length=32)
    make: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    year: int | None = Field(default=None, ge=1950, le=2100)
    fuel_tank_liters: float = Field(default=300.0, gt=0, le=5000)
    odometer_km: float = Field(default=0.0, ge=0)


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    device_type: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    vin: str | None = Field(default=None, max_length=32)
    license_plate: str | None = Field(default=None, max_length=32)
    make: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    year: int | None = Field(default=None, ge=1950, le=2100)
    fuel_tank_liters: float | None = Field(default=None, gt=0, le=5000)
    odometer_km: float | None = Field(default=None, ge=0)
    assigned_driver_id: uuid.UUID | None = None
    # Explicit flag so clients can unassign a driver (null is otherwise "no change")
    unassign_driver: bool = False


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    device_type: str
    owner_id: uuid.UUID | None
    is_active: bool
    registered_at: datetime
    vin: str | None = None
    license_plate: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    fuel_tank_liters: float = 300.0
    odometer_km: float = 0.0
    assigned_driver_id: uuid.UUID | None = None
    api_key_prefix: str | None = None
    last_seen_at: datetime | None = None


class DeviceStatusResponse(BaseModel):
    device_id: uuid.UUID
    cached: bool
    latest_telemetry: dict | None = None


class DeviceApiKeyResponse(BaseModel):
    device_id: uuid.UUID
    api_key: str
    api_key_prefix: str
    note: str = "Store this key now — it cannot be retrieved again."


class LiveDevice(BaseModel):
    id: uuid.UUID
    name: str
    device_type: str
    license_plate: str | None
    is_active: bool
    online: bool
    last_seen_at: datetime | None
    latest: dict | None
    open_alerts: int
    health_score: int
    driver_name: str | None
