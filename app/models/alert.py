import uuid
from datetime import datetime, timezone
import enum

from sqlalchemy import Float, DateTime, ForeignKey, Boolean, Text, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class AlertSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    CRITICAL = "CRITICAL"


class FaultType(str, enum.Enum):
    """Named fault taxonomy derived from sensor pattern matching."""
    COOLANT_LEAK        = "COOLANT_LEAK"
    BATTERY_FAILURE     = "BATTERY_FAILURE"
    TRANSMISSION_STRESS = "TRANSMISSION_STRESS"
    BRAKE_WEAR          = "BRAKE_WEAR"
    ENGINE_STRESS       = "ENGINE_STRESS"
    UNKNOWN_ANOMALY     = "UNKNOWN_ANOMALY"


class FaultConfidence(str, enum.Enum):
    """Confidence level of the fault classification."""
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telemetry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telemetry.id", ondelete="SET NULL"),
        nullable=True,
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        SAEnum(AlertSeverity, name="alertseverity"), nullable=False
    )
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    # JSONB in production (GIN-indexable, native JSON operators).
    # Falls back to plain JSON for SQLite in tests — preserves ORM compatibility.
    affected_metrics: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    # Rule-based fault classification (populated by fault_classifier() in anomaly_service).
    # Nullable so that alerts created before this feature was added remain valid.
    fault_type: Mapped[FaultType | None] = mapped_column(
        SAEnum(FaultType, name="faulttype"), nullable=True
    )
    fault_confidence: Mapped[FaultConfidence | None] = mapped_column(
        SAEnum(FaultConfidence, name="faultconfidence"), nullable=True
    )
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    device: Mapped["Device"] = relationship("Device", back_populates="alerts")  # type: ignore[name-defined]
    telemetry: Mapped["Telemetry | None"] = relationship("Telemetry", back_populates="alert")  # type: ignore[name-defined]
