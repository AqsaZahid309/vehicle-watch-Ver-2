import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import ForbiddenError
from app.core.rate_limiter import check_login_rate_limit
from app.database import get_db
from app.dependencies import get_current_user
from app.models.organization import Organization
from app.models.user import User
from app.redis import get_redis
from app.schemas.user import (
    LoginRequest, MeResponse, OrganizationRead, PasswordChange, RefreshRequest,
    RegisterRequest, TokenResponse, UserRead,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
) -> UserRead:
    """Create a new organization with the caller as its ADMIN."""
    if not settings.allow_public_signup:
        raise ForbiddenError("Public sign-up is disabled — ask an organization admin for an account")
    await check_login_rate_limit(f"register:{_client_ip(request)}", redis)
    user = await AuthService(db).register(data)
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    await check_login_rate_limit(f"{_client_ip(request)}:{data.email.lower()}", redis)
    return await AuthService(db).login(data.email, data.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db, scope="function")) -> TokenResponse:
    return await AuthService(db).refresh(data.refresh_token)


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> MeResponse:
    """The authenticated user's profile and organization."""
    org = await db.get(Organization, current_user.organization_id)
    return MeResponse(
        **UserRead.model_validate(current_user).model_dump(),
        organization=OrganizationRead.model_validate(org),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    await AuthService(db).change_password(current_user, data.current_password, data.new_password)
