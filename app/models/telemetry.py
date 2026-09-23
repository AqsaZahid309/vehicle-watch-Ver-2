import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Telemetry(Base):
    """
    Telemetry table is designed for time-series partitioning by month on recorded_at.
    In production, PostgreSQL declarative range partitioning would be applied via
    raw DDL in Alembic migrations — SQLAlchemy ORM models remain partition-agnostic.
    The composite index on (device_id, recorded_at) covers the most common query
    pattern: "get latest N readings for device X".
    """

    __tablename__ = "telemetry"

    __table_args__ = (
        Index("ix_telemetry_device_recorded", "device_id", "recorded_at"),
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

    # Sensor readings
    gps_lat: Mapped[float] = mapped_column(Float, nullable=False)
    gps_lon: Mapped[float] = mapped_column(Float, nullable=False)
    engine_temp: Mapped[float] = mapped_column(Float, nullable=False)
    rpm: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_level: Mapped[float] = mapped_column(Float, nullable=False)
    battery_voltage: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    vibration: Mapped[float] = mapped_column(Float, nullable=False)

    device: Mapped["Device"] = relationship("Device", back_populates="telemetry_records")  # type: ignore[name-defined]
    alert: Mapped["Alert | None"] = relationship("Alert", back_populates="telemetry", uselist=False)  # type: ignore[name-defined]
