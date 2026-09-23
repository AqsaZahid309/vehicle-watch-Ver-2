from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, TokenResponse, LoginRequest, RefreshRequest
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)) -> UserRead:
    service = AuthService(db)
    user = await service.register(data)
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    service = AuthService(db)
    return await service.login(data.email, data.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    service = AuthService(db)
    return await service.refresh(data.refresh_token)


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    """Returns the authenticated user's profile. Used by clients and the simulator
    to retrieve the user's own ID without decoding the JWT locally."""
    return UserRead.model_validate(current_user)
