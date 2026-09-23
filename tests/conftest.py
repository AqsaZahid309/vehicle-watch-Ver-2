"""
Test fixtures using an in-memory SQLite database (via aiosqlite) and a fake Redis.
This keeps CI fast and dependency-free (no real Postgres/Redis required).

We override the FastAPI dependency injection at the application level so that
every test gets a clean, isolated database session and Redis mock.
"""

import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.organization import Organization
from app.models.device import Device
from app.models.user import User, UserRole
from app.redis import get_redis

# Use SQLite in-memory for tests — fast and requires no external services.
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
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class FakeRedis:
    """Dict-backed stand-in for the redis.asyncio client, covering the calls the app makes."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key):
        return self.store.get(key)

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        return sum(1 for k in keys if self.store.pop(k, None) is not None)

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def eval(self, script, num_keys, *args):
        if "DEL" in script:  # worker lock release: compare-and-delete
            key, token = args[0], args[1]
            if self.store.get(key) == token:
                del self.store[key]
                return 1
            return 0
        return 1  # rate limits always allow in tests

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 0

    async def ping(self):
        return True


@pytest.fixture
def mock_redis() -> FakeRedis:
    """FakeRedis whose `setex` is also call-tracked (tests assert on it)."""
    redis = FakeRedis()
    real_setex = redis.setex
    redis.setex = AsyncMock(side_effect=real_setex)
    return redis


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, mock_redis) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with database and Redis overrides injected."""

    async def override_get_db():
        yield db_session
        await db_session.flush()

    async def override_get_redis():
        yield mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_and_login(client: AsyncClient, email: str, password: str = "testpass123",
                             organization_name: str | None = None) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "organization_name": organization_name},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


async def create_member(client: AsyncClient, admin_token: str, email: str, role: str,
                        password: str = "testpass123") -> str:
    """Admin adds a user with `role` to their organization; returns that user's token."""
    resp = await client.post(
        "/api/v1/users", json={"email": email, "password": password, "role": role}, headers=auth(admin_token)
    )
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> str:
    """Sign up (creates an organization; caller becomes its ADMIN)."""
    return await register_and_login(client, "admin@test.com", organization_name="Test Fleet")


@pytest_asyncio.fixture
async def operator_token(client: AsyncClient, admin_token: str) -> str:
    return await create_member(client, admin_token, "operator@test.com", "OPERATOR")


@pytest_asyncio.fixture
async def viewer_token(client: AsyncClient, admin_token: str) -> str:
    return await create_member(client, admin_token, "viewer@test.com", "VIEWER")


@pytest_asyncio.fixture
async def other_org_token(client: AsyncClient) -> str:
    """Admin of a *different* organization — for tenant-isolation tests."""
    return await register_and_login(client, "intruder@other.com", organization_name="Other Fleet")


@pytest_asyncio.fixture
async def admin_user_id(client: AsyncClient, admin_token: str) -> str:
    resp = await client.get("/api/v1/auth/me", headers=auth(admin_token))
    return resp.json()["id"]


@pytest_asyncio.fixture
async def test_device(client: AsyncClient, admin_token: str, admin_user_id: str) -> dict:
    resp = await client.post(
        "/api/v1/devices",
        json={"name": "Test Truck", "device_type": "truck", "owner_id": admin_user_id},
        headers=auth(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Direct-to-DB helpers for service-level tests ─────────────────────────────

async def make_org(db: AsyncSession, name: str = "Org") -> Organization:
    org = Organization(id=uuid.uuid4(), name=name)
    db.add(org)
    await db.flush()
    return org


async def make_user(db: AsyncSession, org: Organization, email: str,
                    role: UserRole = UserRole.ADMIN) -> User:
    user = User(id=uuid.uuid4(), organization_id=org.id, email=email, hashed_password="x", role=role)
    db.add(user)
    await db.flush()
    return user


async def make_device(db: AsyncSession, org: Organization, owner: User | None = None,
                      name: str = "Test Truck", device_type: str = "truck") -> Device:
    device = Device(
        id=uuid.uuid4(), organization_id=org.id, owner_id=owner.id if owner else None,
        name=name, device_type=device_type,
    )
    db.add(device)
    await db.flush()
    return device
