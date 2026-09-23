import uuid

from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    decode_token,
)
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.user import RegisterRequest, TokenResponse
from app.services.audit_service import record_audit

# Dummy hash used to ensure verify_password is always called during login,
# even when the email doesn't exist. This prevents user-enumeration via
# timing differences (bcrypt is slow; skipping it when user is absent
# would make "no such user" responses ~100ms faster than "wrong password").
_DUMMY_HASH = "$2b$12$KixPH2GhKvRiV2gGIR7FiuFHITuGBcnEm3Jt3LMiVMKIWEBHxoVEq"


def _tokens_for(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, str(user.organization_id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _ensure_email_free(self, email: str) -> None:
        existing = await self._db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            raise ConflictError(f"Email '{email}' is already registered")

    async def register(self, data: RegisterRequest) -> User:
        """
        Self-service sign-up: always creates a *new* organization and makes the
        caller its ADMIN. Joining an existing organization requires an admin
        of that organization to create the account (see UserService.create).
        """
        email = data.email.lower()
        await self._ensure_email_free(email)

        org = Organization(name=data.organization_name or f"{email.split('@')[0]}'s fleet")
        self._db.add(org)
        await self._db.flush()

        user = User(
            organization_id=org.id,
            email=email,
            full_name=data.full_name,
            hashed_password=hash_password(data.password),
            role=UserRole.ADMIN,
        )
        self._db.add(user)
        await self._db.flush()
        await self._db.refresh(user)
        record_audit(self._db, user, "organization.created", "organization", org.id, {"name": org.name})
        return user

    async def login(self, email: str, password: str) -> TokenResponse:
        result = await self._db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        # Always call verify_password — even when user doesn't exist — to prevent
        # user-enumeration via response timing.
        candidate_hash = user.hashed_password if user else _DUMMY_HASH
        password_ok = verify_password(password, candidate_hash)

        if not user or not password_ok:
            raise UnauthorizedError("Invalid email or password")
        if not user.is_active:
            raise UnauthorizedError("Account is disabled")

        return _tokens_for(user)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_token(refresh_token)
        except JWTError:
            raise UnauthorizedError("Invalid or expired refresh token")

        if payload.get("type") != "refresh":
            raise UnauthorizedError("Expected refresh token")

        user_id_str: str | None = payload.get("sub")
        if not user_id_str:
            raise UnauthorizedError("Token has no subject")

        try:
            user_id = uuid.UUID(user_id_str)
        except ValueError:
            raise UnauthorizedError("Invalid token subject")

        result = await self._db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise UnauthorizedError("User not found")

        return _tokens_for(user)

    async def change_password(self, user: User, current: str, new: str) -> None:
        if not verify_password(current, user.hashed_password):
            raise UnauthorizedError("Current password is incorrect")
        user.hashed_password = hash_password(new)
        record_audit(self._db, user, "user.password_changed", "user", user.id)
        await self._db.flush()
