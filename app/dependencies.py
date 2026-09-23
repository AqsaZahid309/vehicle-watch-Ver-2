import uuid
from collections.abc import Callable

from fastapi import Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError, ForbiddenError
from app.core.security import api_key_matches, decode_token, parse_api_key_prefix
from app.database import get_db
from app.models.device import Device
from app.models.user import User, UserRole
from app.redis import get_redis

__all__ = [
    "get_db", "get_redis", "get_current_user", "require_roles", "require_admin",
    "require_manager", "require_technician", "require_operator", "get_device_from_api_key",
    "get_optional_user", "WRITE_ROLES",
]

_bearer = HTTPBearer(auto_error=False)


async def user_from_token(token: str, db: AsyncSession) -> User:
    try:
        payload = decode_token(token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")

    if payload.get("type") != "access":
        raise UnauthorizedError("Expected access token")

    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise UnauthorizedError("Token has no subject")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise UnauthorizedError("Invalid token subject")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise UnauthorizedError("Account is disabled")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise UnauthorizedError("Bearer token missing")
    return await user_from_token(credentials.credentials, db)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not credentials:
        return None
    return await user_from_token(credentials.credentials, db)


def require_roles(*roles: UserRole) -> Callable[..., User]:
    allowed = set(roles)

    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise ForbiddenError(
                f"Requires one of: {', '.join(sorted(r.value for r in allowed))}"
            )
        return current_user

    return _dep


# Role tiers (each includes the ones above it)
require_admin = require_roles(UserRole.ADMIN)
require_manager = require_roles(UserRole.ADMIN, UserRole.MANAGER)
require_technician = require_roles(UserRole.ADMIN, UserRole.MANAGER, UserRole.TECHNICIAN)
require_operator = require_roles(
    UserRole.ADMIN, UserRole.MANAGER, UserRole.TECHNICIAN, UserRole.OPERATOR
)
WRITE_ROLES = {UserRole.ADMIN, UserRole.MANAGER, UserRole.TECHNICIAN, UserRole.OPERATOR}


async def get_device_from_api_key(
    x_device_key: str | None = Header(default=None, alias="X-Device-Key"),
    db: AsyncSession = Depends(get_db),
) -> Device | None:
    """Resolve the device behind an `X-Device-Key` header, or None if no header was sent."""
    if not x_device_key:
        return None
    prefix = parse_api_key_prefix(x_device_key)
    if not prefix:
        raise UnauthorizedError("Malformed device key")
    result = await db.execute(select(Device).where(Device.api_key_prefix == prefix))
    device = result.scalar_one_or_none()
    if not device or not device.api_key_hash or not api_key_matches(x_device_key, device.api_key_hash):
        raise UnauthorizedError("Invalid device key")
    if not device.is_active:
        raise ForbiddenError("Device is deactivated")
    return device
