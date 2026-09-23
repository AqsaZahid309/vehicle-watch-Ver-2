import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class TelemetryCreate(BaseModel):
    gps_lat: float
    gps_lon: float
    engine_temp: float
    rpm: float
    fuel_level: float
    battery_voltage: float
    speed: float
    vibration: float

    @field_validator("gps_lat")
    @classmethod
    def validate_lat(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @field_validator("gps_lon")
    @classmethod
    def validate_lon(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("Longitude must be between -180 and 180")
        return v

    @field_validator("fuel_level")
    @classmethod
    def validate_fuel(cls, v: float) -> float:
        if not 0 <= v <= 100:
            raise ValueError("Fuel level must be between 0 and 100")
        return v


class TelemetryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    recorded_at: datetime
    gps_lat: float
    gps_lon: float
    engine_temp: float
    rpm: float
    fuel_level: float
    battery_voltage: float
    speed: float
    vibration: float


class PaginatedTelemetry(BaseModel):
    items: list[TelemetryRead]
    total: int
    page: int
    page_size: int
    pages: int
