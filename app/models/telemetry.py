import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, DateTime, ForeignKey, Index, Integer, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class Telemetry(Base):
    """
    One sensor reading. `recorded_at` is the device's own timestamp, so readings
    buffered while a vehicle was offline keep their true time when uploaded later.
    The (device_id, recorded_at) unique constraint makes re-sent batches idempotent.
    """

    __tablename__ = "telemetry"

    __table_args__ = (
        Index("ix_telemetry_device_recorded", "device_id", "recorded_at"),
        UniqueConstraint("device_id", "recorded_at", name="uq_telemetry_device_recorded"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Core sensors (required)
    gps_lat: Mapped[float] = mapped_column(Float, nullable=False)
    gps_lon: Mapped[float] = mapped_column(Float, nullable=False)
    engine_temp: Mapped[float] = mapped_column(Float, nullable=False)
    rpm: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_level: Mapped[float] = mapped_column(Float, nullable=False)
    battery_voltage: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    vibration: Mapped[float] = mapped_column(Float, nullable=False)

    # Extended sensors (optional — not every telematics unit reports them)
    oil_pressure: Mapped[float | None] = mapped_column(Float, nullable=True)      # psi
    coolant_level: Mapped[float | None] = mapped_column(Float, nullable=True)     # %
    tire_pressure: Mapped[float | None] = mapped_column(Float, nullable=True)     # psi (lowest tyre)
    ambient_temp: Mapped[float | None] = mapped_column(Float, nullable=True)      # °C
    dtc_codes: Mapped[list | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )

    device: Mapped["Device"] = relationship("Device", back_populates="telemetry_records")  # type: ignore[name-defined]
    alert: Mapped["Alert | None"] = relationship("Alert", back_populates="telemetry", uselist=False)  # type: ignore[name-defined]


class TelemetryHourly(Base):
    """
    Hourly rollups maintained by the worker. Dashboards and long-range charts
    read from here instead of scanning the raw telemetry table.
    """

    __tablename__ = "telemetry_hourly"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_engine_temp: Mapped[float] = mapped_column(Float, nullable=False)
    max_engine_temp: Mapped[float] = mapped_column(Float, nullable=False)
    avg_rpm: Mapped[float] = mapped_column(Float, nullable=False)
    max_rpm: Mapped[float] = mapped_column(Float, nullable=False)
    avg_speed: Mapped[float] = mapped_column(Float, nullable=False)
    max_speed: Mapped[float] = mapped_column(Float, nullable=False)
    avg_fuel_level: Mapped[float] = mapped_column(Float, nullable=False)
    avg_battery_voltage: Mapped[float] = mapped_column(Float, nullable=False)
    min_battery_voltage: Mapped[float] = mapped_column(Float, nullable=False)
    avg_vibration: Mapped[float] = mapped_column(Float, nullable=False)
    max_vibration: Mapped[float] = mapped_column(Float, nullable=False)
