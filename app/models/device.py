import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Float, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # User responsible for the vehicle (optional since the move to org-level access).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Vehicle details
    vin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    license_plate: Mapped[str | None] = mapped_column(String(32), nullable=True)
    make: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fuel_tank_liters: Mapped[float] = mapped_column(Float, nullable=False, default=300.0)
    odometer_km: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assigned_driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )

    # Device credentials — only a SHA-256 hash of the key is stored. The prefix
    # identifies the key in O(1) and is safe to display in the UI.
    api_key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Worker watermarks — persisted so a restart (or a second worker instance)
    # resumes where the last successful cycle stopped instead of rescanning history.
    anomaly_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trip_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped["User | None"] = relationship("User", back_populates="devices")  # type: ignore[name-defined]
    assigned_driver: Mapped["Driver | None"] = relationship("Driver", lazy="joined")  # type: ignore[name-defined]
    telemetry_records: Mapped[list["Telemetry"]] = relationship(  # type: ignore[name-defined]
        "Telemetry", back_populates="device", lazy="select", passive_deletes=True
    )
    alerts: Mapped[list["Alert"]] = relationship(  # type: ignore[name-defined]
        "Alert", back_populates="device", lazy="select", passive_deletes=True
    )
