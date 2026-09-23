import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, LargeBinary, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class ModelVersion(Base):
    """
    Registry entry for one trained anomaly-detection model.

    The signed model artifact lives here (Redis is only a cache), so a model
    survives Redis restarts and can be pinned as a known-good baseline. A pinned
    model is never replaced by automatic retraining, which stops a slowly
    degrading vehicle from teaching the model that its own degradation is normal.
    """

    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # DEVICE models are trained on one vehicle; CLASS models on every vehicle of a
    # device_type and are the cold-start fallback for vehicles without history.
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="DEVICE")
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True
    )
    device_type: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)  # INITIAL | SCHEDULED | DRIFT | MANUAL
    n_train: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feature_stats: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    drift_psi: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    artifact: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
