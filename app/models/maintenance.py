import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Float, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkOrderStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD = "ON_HOLD"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class WorkOrderPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


# Root causes a technician can record. Fault types from the detector, plus two
# outcomes that are not faults. NO_FAULT_FOUND marks the source alert as a false positive.
ROOT_CAUSES = [
    "COOLANT_LEAK", "BATTERY_FAILURE", "TRANSMISSION_STRESS", "BRAKE_WEAR",
    "ENGINE_STRESS", "WHEEL_BEARING", "LOW_OIL_PRESSURE", "TIRE_PRESSURE",
    "OTHER_FAULT", "PREVENTIVE_SERVICE", "NO_FAULT_FOUND",
]


class WorkOrder(Base):
    """
    Closes the loop on an alert: who fixed what, at what cost, and what the
    confirmed root cause was. Resolved work orders feed back into the alert's
    TRUE/FALSE_POSITIVE label, which drives the model precision metrics.
    """

    __tablename__ = "work_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # human-friendly per-org sequence
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_schedules.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WorkOrderStatus.OPEN.value, index=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default=WorkOrderPriority.MEDIUM.value)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    root_cause: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    parts_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    labor_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    downtime_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped["Device"] = relationship("Device", lazy="joined")  # type: ignore[name-defined]


class ServiceSchedule(Base):
    """
    Preventive maintenance rule, e.g. "Oil change every 15,000 km or 180 days".
    The worker opens a work order automatically when a schedule falls due.
    """

    __tablename__ = "service_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_service_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_service_km: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
