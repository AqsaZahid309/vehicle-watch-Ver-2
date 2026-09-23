import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.user import OrganizationRead, OrganizationUpdate, UserCreate, UserRead, UserUpdate
from app.services.user_service import UserService

router = APIRouter(tags=["Users & Organization"])


@router.get("/users", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[UserRead]:
    return [UserRead.model_validate(u) for u in await UserService(db).list_users(current_user)]


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
) -> UserRead:
    """Add a teammate to your organization with a chosen role."""
    return UserRead.model_validate(await UserService(db).create(data, admin))


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID, data: UserUpdate,
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
) -> UserRead:
    return UserRead.model_validate(await UserService(db).update(user_id, data, admin))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
) -> None:
    await UserService(db).delete(user_id, admin)


@router.get("/organization", response_model=OrganizationRead)
async def get_organization(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> OrganizationRead:
    return OrganizationRead.model_validate(await UserService(db).get_org(current_user))


@router.patch("/organization", response_model=OrganizationRead)
async def update_organization(
    data: OrganizationUpdate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
) -> OrganizationRead:
    return OrganizationRead.model_validate(await UserService(db).update_org(data, admin))


@router.get("/audit-logs")
async def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> list[dict]:
    rows = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.organization_id == admin.organization_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(a.id), "created_at": a.created_at.isoformat(), "user_email": a.user_email,
            "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "details": a.details,
        }
        for a in rows
    ]
