import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.fleet import GeofenceKind


# ── Drivers ───────────────────────────────────────────────────────────────────

class DriverCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    license_number: str | None = Field(default=None, max_length=64)


class DriverUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    license_number: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class DriverRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str | None
    phone: str | None
    license_number: str | None
    is_active: bool
    created_at: datetime


class DriverScore(BaseModel):
    driver_id: uuid.UUID | None
    driver_name: str
    trips: int
    distance_km: float
    driving_hours: float
    idle_pct: float
    harsh_accel_per_100km: float
    harsh_brake_per_100km: float
    overspeed_pct: float
    over_rev_events: int
    fuel_l_per_100km: float | None
    score: float


# ── Trips ─────────────────────────────────────────────────────────────────────

class TripRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str | None = None
    driver_id: uuid.UUID | None
    driver_name: str | None = None
    started_at: datetime
    ended_at: datetime
    is_open: bool
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    distance_km: float
    duration_seconds: float
    moving_seconds: float
    idle_seconds: float
    max_speed: float
    avg_speed: float
    harsh_accel_count: int
    harsh_brake_count: int
    overspeed_seconds: float
    over_rev_count: int
    fuel_used_liters: float
    score: float


class PaginatedTrips(BaseModel):
    items: list[TripRead]
    total: int
    page: int
    page_size: int
    pages: int


class TripPoint(BaseModel):
    t: datetime
    lat: float
    lon: float
    speed: float


# ── Geofences ─────────────────────────────────────────────────────────────────

class GeofenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: GeofenceKind = GeofenceKind.CUSTOMER
    shape: str = Field(default="CIRCLE", pattern="^(CIRCLE|POLYGON)$")
    center_lat: float | None = Field(default=None, ge=-90, le=90)
    center_lon: float | None = Field(default=None, ge=-180, le=180)
    radius_m: float | None = Field(default=None, gt=0, le=200_000)
    polygon: list[list[float]] | None = None
    alert_on_enter: bool = False
    alert_on_exit: bool = False
    speed_limit_kmh: float | None = Field(default=None, gt=0, le=300)

    @model_validator(mode="after")
    def _shape_fields(self) -> "GeofenceCreate":
        if self.shape == "CIRCLE":
            if self.center_lat is None or self.center_lon is None or not self.radius_m:
                raise ValueError("CIRCLE geofences need center_lat, center_lon and radius_m")
        else:
            if not self.polygon or len(self.polygon) < 3:
                raise ValueError("POLYGON geofences need at least 3 [lat, lon] points")
            for pt in self.polygon:
                if len(pt) != 2 or not (-90 <= pt[0] <= 90) or not (-180 <= pt[1] <= 180):
                    raise ValueError("Polygon points must be [lat, lon] pairs")
        return self


class GeofenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: GeofenceKind | None = None
    radius_m: float | None = Field(default=None, gt=0, le=200_000)
    alert_on_enter: bool | None = None
    alert_on_exit: bool | None = None
    speed_limit_kmh: float | None = Field(default=None, gt=0, le=300)


class GeofenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: GeofenceKind
    shape: str
    center_lat: float | None
    center_lon: float | None
    radius_m: float | None
    polygon: list[list[float]] | None
    alert_on_enter: bool
    alert_on_exit: bool
    speed_limit_kmh: float | None
    created_at: datetime
    vehicles_inside: int = 0


class GeofenceEventRead(BaseModel):
    id: uuid.UUID
    geofence_id: uuid.UUID
    geofence_name: str
    geofence_kind: str
    device_id: uuid.UUID
    device_name: str
    event: str
    occurred_at: datetime
    lat: float
    lon: float
    speed: float


# ── Fuel ──────────────────────────────────────────────────────────────────────

class FuelEventRead(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str
    event: str
    occurred_at: datetime
    level_before: float
    level_after: float
    liters: float
    lat: float
    lon: float
    reviewed: bool


class FuelVehicleStat(BaseModel):
    device_id: uuid.UUID
    device_name: str
    distance_km: float
    fuel_used_liters: float
    l_per_100km: float | None
    fuel_cost: float
    cost_per_km: float | None
    refuels: int
    theft_suspected: int
    current_level: float | None
