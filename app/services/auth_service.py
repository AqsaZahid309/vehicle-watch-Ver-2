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
from app.models.user import User
from app.schemas.user import UserCreate, TokenResponse

# Dummy hash used to ensure verify_password is always called during login,
# even when the email doesn't exist. This prevents user-enumeration via
# timing differences (bcrypt is slow; skipping it when user is absent
# would make "no such user" responses ~100ms faster than "wrong password").
_DUMMY_HASH = "$2b$12$KixPH2GhKvRiV2gGIR7FiuFHITuGBcnEm3Jt3LMiVMKIWEBHxoVEq"


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, data: UserCreate) -> User:
        existing = await self._db.execute(
            select(User).where(User.email == data.email)
        )
        if existing.scalar_one_or_none():
            raise ConflictError(f"Email '{data.email}' is already registered")

        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            role=data.role,
        )
        self._db.add(user)
        await self._db.flush()
        await self._db.refresh(user)
        return user

    async def login(self, email: str, password: str) -> TokenResponse:
        result = await self._db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        # Always call verify_password — even when user doesn't exist — to prevent
        # user-enumeration via response timing. Without this, an attacker can
        # detect valid emails because bcrypt is intentionally slow; skipping it
        # for unknown emails makes those responses ~100ms faster.
        candidate_hash = user.hashed_password if user else _DUMMY_HASH
        password_ok = verify_password(password, candidate_hash)

        if not user or not password_ok:
            raise UnauthorizedError("Invalid email or password")

        return TokenResponse(
            access_token=create_access_token(str(user.id), user.role.value),
            refresh_token=create_refresh_token(str(user.id)),
        )

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

        result = await self._db.execute(
            select(User).where(User.id == uuid.UUID(user_id_str))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UnauthorizedError("User not found")

        return TokenResponse(
            access_token=create_access_token(str(user.id), user.role.value),
            refresh_token=create_refresh_token(str(user.id)),
        )
