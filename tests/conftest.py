"""
Test fixtures using an in-memory SQLite database (via aiosqlite) and a fake Redis.
This keeps CI fast and dependency-free (no real Postgres/Redis required).

We override the FastAPI dependency injection at the application level so that
every test gets a clean, isolated database session and Redis mock.
"""

import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.redis import get_redis

# Use SQLite in-memory for tests — fast and requires no external services.
# We use aiosqlite driver for async compatibility.
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def mock_redis() -> AsyncMock:
    """
    Fake Redis with call tracking for aioredis usage patterns.
    We use AsyncMock with side_effect to preserve call recording
    (call_count, assert_called_once, etc.) while injecting real behavior.
    """
    redis = AsyncMock()
    _store: dict[str, str] = {}

    async def fake_get(key: str):
        return _store.get(key)

    async def fake_setex(key: str, ttl: int, value: str):
        _store[key] = value

    async def fake_eval(script, num_keys, *args):
        return 1  # Always allow in tests

    # Assign as AsyncMock with side_effect so call tracking is preserved
    redis.get = AsyncMock(side_effect=fake_get)
    redis.setex = AsyncMock(side_effect=fake_setex)
    redis.eval = AsyncMock(side_effect=fake_eval)
    return redis


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, mock_redis: AsyncMock) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with database and Redis overrides injected."""

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> str:
    """Register an admin user and return an access token."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "admin@test.com", "password": "testpass123", "role": "ADMIN"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "testpass123"},
    )
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def operator_token(client: AsyncClient) -> str:
    """Register an operator user and return an access token."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "operator@test.com", "password": "testpass123", "role": "OPERATOR"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "operator@test.com", "password": "testpass123"},
    )
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def admin_user_id(client: AsyncClient, admin_token: str) -> str:
    """Return the UUID of the admin user."""
    from jose import jwt
    from app.config import get_settings
    settings = get_settings()
    payload = jwt.decode(admin_token, settings.secret_key, algorithms=[settings.algorithm])
    return payload["sub"]


@pytest_asyncio.fixture
async def test_device(client: AsyncClient, admin_token: str, admin_user_id: str) -> dict:
    """Create a device and return its JSON representation."""
    resp = await client.post(
        "/api/v1/devices",
        json={"name": "Test Truck", "device_type": "truck", "owner_id": admin_user_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    return resp.json()
