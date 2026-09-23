import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User


def record_audit(
    db: AsyncSession,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
    organization_id: uuid.UUID | None = None,
) -> None:
    """Stage an audit entry on the caller's session — it commits with the action it describes."""
    org_id = organization_id or (actor.organization_id if actor else None)
    if org_id is None:
        return
    db.add(
        AuditLog(
            organization_id=org_id,
            user_id=actor.id if actor else None,
            user_email=actor.email if actor else "system",
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            details=details or {},
        )
    )
