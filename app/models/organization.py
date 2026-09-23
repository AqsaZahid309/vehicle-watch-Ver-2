import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Organization(Base):
    """
    Tenant boundary. Every device, user, driver, geofence, work order and
    notification channel belongs to exactly one organization, and every query
    in the service layer is scoped by the requester's organization_id.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    fuel_price_per_liter: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    # Minutes an unacknowledged CRITICAL alert may sit before it is escalated.
    escalation_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    users: Mapped[list["User"]] = relationship("User", back_populates="organization")  # type: ignore[name-defined]
