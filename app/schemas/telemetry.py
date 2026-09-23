import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

# How far a device clock may run ahead of the server, and how old buffered data may be.
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_BACKFILL_AGE = timedelta(days=7)


class TelemetryCreate(BaseModel):
    # Device-side timestamp. Omit to use server receive time. Buffered readings
    # from a vehicle that was out of coverage should send their real capture time.
    recorded_at: datetime | None = None

    gps_lat: float
    gps_lon: float
    engine_temp: float = Field(ge=-60, le=250)
    rpm: float = Field(ge=0, le=12000)
    fuel_level: float
    battery_voltage: float = Field(ge=0, le=60)
    speed: float = Field(ge=0, le=400)
    vibration: float = Field(ge=0, le=100)

    # Optional extended sensors
    oil_pressure: float | None = Field(default=None, ge=0, le=200)
    coolant_level: float | None = Field(default=None, ge=0, le=100)
    tire_pressure: float | None = Field(default=None, ge=0, le=200)
    ambient_temp: float | None = Field(default=None, ge=-80, le=80)
    dtc_codes: list[str] | None = Field(default=None, max_length=32)

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

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if v > now + _MAX_CLOCK_SKEW:
            raise ValueError("recorded_at is in the future — check the device clock")
        if v < now - _MAX_BACKFILL_AGE:
            raise ValueError("recorded_at is older than the 7-day backfill window")
        return v

    @field_validator("dtc_codes")
    @classmethod
    def normalise_dtc(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [c.strip().upper()[:8] for c in v if c and c.strip()]


class TelemetryBatch(BaseModel):
    readings: list[TelemetryCreate] = Field(min_length=1, max_length=500)


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
    oil_pressure: float | None = None
    coolant_level: float | None = None
    tire_pressure: float | None = None
    ambient_temp: float | None = None
    dtc_codes: list[str] | None = None


class BatchIngestResponse(BaseModel):
    accepted: int
    duplicates: int


class PaginatedTelemetry(BaseModel):
    items: list[TelemetryRead]
    total: int
    page: int
    page_size: int
    pages: int
