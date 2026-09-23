import uuid
from datetime import datetime, timezone
import enum

from sqlalchemy import String, Boolean, Enum as SAEnum, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class UserRole(str, enum.Enum):
    """
    Organization-scoped roles, most to least privileged:

    ADMIN       — everything, including users, org settings and notification channels
    MANAGER     — fleet management: devices, drivers, geofences, work orders, ML models
    TECHNICIAN  — works maintenance orders, acknowledges alerts
    OPERATOR    — monitors the fleet, acknowledges alerts, raises work orders
    VIEWER      — read-only
    """

    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    TECHNICIAN = "TECHNICIAN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="userrole"), nullable=False, default=UserRole.OPERATOR
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    organization: Mapped["Organization"] = relationship("Organization", back_populates="users")  # type: ignore[name-defined]
    # passive_deletes: let the database apply ON DELETE SET NULL instead of the
    # ORM lazy-loading every device (which is not allowed under asyncio).
    devices: Mapped[list["Device"]] = relationship(  # type: ignore[name-defined]
        "Device", back_populates="owner", lazy="select", passive_deletes=True
    )
