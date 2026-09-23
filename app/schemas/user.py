import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict

from app.models.user import UserRole


def _check_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")
    return v


class RegisterRequest(BaseModel):
    """
    Public sign-up. Creates a new organization and makes the caller its ADMIN.
    There is deliberately no `role` field: a public endpoint must never let the
    caller choose their own privileges inside an existing tenant.
    """

    model_config = ConfigDict(extra="ignore")

    email: EmailStr
    password: str
    full_name: str | None = Field(default=None, max_length=255)
    organization_name: str | None = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password(v)


class UserCreate(BaseModel):
    """Admin-created user inside the admin's own organization."""

    email: EmailStr
    password: str
    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole = UserRole.OPERATOR

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password(v)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _check_password(v)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: UserRole
    is_active: bool = True
    organization_id: uuid.UUID
    created_at: datetime


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    currency: str
    fuel_price_per_liter: float
    escalation_minutes: int
    created_at: datetime


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    fuel_price_per_liter: float | None = Field(default=None, ge=0)
    escalation_minutes: int | None = Field(default=None, ge=1, le=1440)


class MeResponse(UserRead):
    organization: OrganizationRead


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str
