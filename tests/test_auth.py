import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "newuser@test.com", "password": "password123", "role": "OPERATOR"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "newuser@test.com"
    assert data["role"] == "OPERATOR"
    assert "id" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "dup@test.com", "password": "password123"}
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "user@test.com", "password": "short"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "login@test.com", "password": "password123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "login@test.com", "password": "password123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "user2@test.com", "password": "correctpass"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "user2@test.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@test.com", "password": "anything"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "refresh@test.com", "password": "password123"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "refresh@test.com", "password": "password123"},
    )
    refresh_token = login_resp.json()["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_refresh_with_access_token_rejected(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "badrefresh@test.com", "password": "password123"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "badrefresh@test.com", "password": "password123"},
    )
    access_token = login_resp.json()["access_token"]

    # Using access token where refresh token is expected should fail
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401
