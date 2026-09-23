import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.user import OrganizationUpdate, UserCreate, UserUpdate
from app.services.audit_service import record_audit


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_users(self, requester: User) -> list[User]:
        result = await self._db.execute(
            select(User)
            .where(User.organization_id == requester.organization_id)
            .order_by(User.created_at)
        )
        return list(result.scalars().all())

    async def _get(self, user_id: uuid.UUID, requester: User) -> User:
        result = await self._db.execute(
            select(User).where(User.id == user_id, User.organization_id == requester.organization_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("User", str(user_id))
        return user

    async def create(self, data: UserCreate, requester: User) -> User:
        email = data.email.lower()
        if (await self._db.execute(select(User).where(User.email == email))).scalar_one_or_none():
            raise ConflictError(f"Email '{email}' is already registered")
        user = User(
            organization_id=requester.organization_id,
            email=email,
            full_name=data.full_name,
            hashed_password=hash_password(data.password),
            role=data.role,
        )
        self._db.add(user)
        await self._db.flush()
        await self._db.refresh(user)
        record_audit(self._db, requester, "user.created", "user", user.id, {"email": email, "role": data.role.value})
        return user

    async def _admin_count(self, org_id: uuid.UUID) -> int:
        return (
            await self._db.execute(
                select(func.count()).where(
                    User.organization_id == org_id,
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            )
        ).scalar_one()

    async def update(self, user_id: uuid.UUID, data: UserUpdate, requester: User) -> User:
        user = await self._get(user_id, requester)
        losing_admin = user.role == UserRole.ADMIN and (
            (data.role is not None and data.role != UserRole.ADMIN) or data.is_active is False
        )
        if losing_admin and await self._admin_count(user.organization_id) <= 1:
            raise AppError("An organization must keep at least one active admin", 400)

        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(user, field, value)
        await self._db.flush()
        await self._db.refresh(user)
        record_audit(self._db, requester, "user.updated", "user", user.id,
                     {k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()})
        return user

    async def delete(self, user_id: uuid.UUID, requester: User) -> None:
        user = await self._get(user_id, requester)
        if user.id == requester.id:
            raise AppError("You cannot delete your own account", 400)
        if user.role == UserRole.ADMIN and await self._admin_count(user.organization_id) <= 1:
            raise AppError("An organization must keep at least one active admin", 400)
        record_audit(self._db, requester, "user.deleted", "user", user.id, {"email": user.email})
        await self._db.delete(user)
        await self._db.flush()

    async def get_org(self, requester: User) -> Organization:
        org = await self._db.get(Organization, requester.organization_id)
        if not org:
            raise NotFoundError("Organization")
        return org

    async def update_org(self, data: OrganizationUpdate, requester: User) -> Organization:
        org = await self.get_org(requester)
        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(org, field, value)
        record_audit(self._db, requester, "organization.updated", "organization", org.id, changes)
        await self._db.flush()
        await self._db.refresh(org)
        return org
