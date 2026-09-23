"""Drivers, trips, geofences and fuel events — the operational side of the fleet."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.utils import as_utc
from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Trip(Base):
    """
    A continuous period of operation, split on telemetry gaps longer than
    TRIP_GAP_MINUTES. Built incrementally by the worker — the `last_*` columns
    hold the state needed to continue an open trip on the next cycle.
    """

    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_lat: Mapped[float] = mapped_column(Float, nullable=False)
    start_lon: Mapped[float] = mapped_column(Float, nullable=False)
    end_lat: Mapped[float] = mapped_column(Float, nullable=False)
    end_lon: Mapped[float] = mapped_column(Float, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    moving_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_speed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    speed_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    harsh_accel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    harsh_brake_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overspeed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    over_rev_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fuel_used_liters: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    # Continuation state
    last_speed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_fuel_level: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    device: Mapped["Device"] = relationship("Device", lazy="joined")  # type: ignore[name-defined]
    driver: Mapped["Driver | None"] = relationship("Driver", lazy="joined")

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (as_utc(self.ended_at) - as_utc(self.started_at)).total_seconds())

    @property
    def avg_speed(self) -> float:
        return round(self.speed_sum / self.point_count, 1) if self.point_count else 0.0


class GeofenceKind(str, enum.Enum):
    DEPOT = "DEPOT"
    CUSTOMER = "CUSTOMER"
    SERVICE = "SERVICE"
    RESTRICTED = "RESTRICTED"


class Geofence(Base):
    __tablename__ = "geofences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=GeofenceKind.CUSTOMER.value)
    # CIRCLE uses center + radius; POLYGON uses `polygon` ([[lat, lon], ...]).
    shape: Mapped[str] = mapped_column(String(10), nullable=False, default="CIRCLE")
    center_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    center_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    polygon: Mapped[list | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    alert_on_enter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alert_on_exit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    speed_limit_kmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class GeofenceEvent(Base):
    __tablename__ = "geofence_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    geofence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("geofences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(20), nullable=False)  # ENTER | EXIT | SPEEDING
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    geofence: Mapped["Geofence"] = relationship("Geofence", lazy="joined")
    device: Mapped["Device"] = relationship("Device", lazy="joined")  # type: ignore[name-defined]


class DeviceGeofenceState(Base):
    """Whether a device is currently inside a geofence — used to emit ENTER/EXIT only on transitions."""

    __tablename__ = "device_geofence_state"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    geofence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("geofences.id", ondelete="CASCADE"), primary_key=True
    )
    inside: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True while the vehicle is inside and above the fence's speed limit — so one
    # speeding episode produces one SPEEDING event, not one per reading.
    speeding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class FuelEventType(str, enum.Enum):
    REFUEL = "REFUEL"
    THEFT_SUSPECTED = "THEFT_SUSPECTED"


class FuelEvent(Base):
    __tablename__ = "fuel_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    level_before: Mapped[float] = mapped_column(Float, nullable=False)
    level_after: Mapped[float] = mapped_column(Float, nullable=False)
    liters: Mapped[float] = mapped_column(Float, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    device: Mapped["Device"] = relationship("Device", lazy="joined")  # type: ignore[name-defined]
