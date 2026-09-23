import uuid

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError, ForbiddenError
from app.core.security import decode_token
from app.database import get_db
from app.models.user import User, UserRole
from app.redis import get_redis

import redis.asyncio as aioredis

__all__ = ["get_db", "get_redis", "get_current_user", "require_admin", "require_operator"]

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise UnauthorizedError("Bearer token missing")

    try:
        payload = decode_token(credentials.credentials)
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

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return current_user


def require_operator(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in (UserRole.ADMIN, UserRole.OPERATOR):
        raise ForbiddenError("Operator access required")
    return current_user
