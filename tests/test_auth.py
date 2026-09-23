import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient) -> None:
    """Sign-up creates a new organization and makes the caller its ADMIN."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "newuser@test.com", "password": "password123", "organization_name": "Acme Haulage"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "newuser@test.com"
    assert data["role"] == "ADMIN"
    assert data["organization_id"]
    assert "id" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_each_signup_gets_its_own_organization(client: AsyncClient) -> None:
    a = await client.post("/api/v1/auth/register", json={"email": "a@test.com", "password": "password123"})
    b = await client.post("/api/v1/auth/register", json={"email": "b@test.com", "password": "password123"})
    assert a.json()["organization_id"] != b.json()["organization_id"]


@pytest.mark.asyncio
async def test_register_ignores_client_supplied_role(client: AsyncClient, admin_token: str) -> None:
    """
    Regression test for the privilege-escalation bug: a public sign-up used to accept
    `role: ADMIN` and grant it. Now the role field is ignored and the account lands in
    a brand-new organization, so it can never become admin of an existing fleet.
    """
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "attacker@test.com", "password": "password123", "role": "ADMIN"},
    )
    assert resp.status_code == 201
    login = await client.post("/api/v1/auth/login", json={"email": "attacker@test.com", "password": "password123"})
    attacker = login.json()["access_token"]
    me_admin = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})).json()
    me_attacker = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {attacker}"})).json()
    assert me_attacker["organization_id"] != me_admin["organization_id"]
    devices = await client.get("/api/v1/devices", headers={"Authorization": f"Bearer {attacker}"})
    assert devices.json() == []


@pytest.mark.asyncio
async def test_me_includes_organization(client: AsyncClient, admin_token: str) -> None:
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["organization"]["name"] == "Test Fleet"


@pytest.mark.asyncio
async def test_change_password(client: AsyncClient, admin_token: str) -> None:
    h = {"Authorization": f"Bearer {admin_token}"}
    bad = await client.post("/api/v1/auth/change-password",
                            json={"current_password": "wrong-pass", "new_password": "newpass1234"}, headers=h)
    assert bad.status_code == 401
    ok = await client.post("/api/v1/auth/change-password",
                           json={"current_password": "testpass123", "new_password": "newpass1234"}, headers=h)
    assert ok.status_code == 204
    login = await client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "newpass1234"})
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_disabled_user_cannot_log_in(client: AsyncClient, admin_token: str, operator_token: str) -> None:
    h = {"Authorization": f"Bearer {admin_token}"}
    users = (await client.get("/api/v1/users", headers=h)).json()
    op = next(u for u in users if u["email"] == "operator@test.com")
    resp = await client.patch(f"/api/v1/users/{op['id']}", json={"is_active": False}, headers=h)
    assert resp.status_code == 200
    login = await client.post("/api/v1/auth/login", json={"email": "operator@test.com", "password": "testpass123"})
    assert login.status_code == 401
    # Existing tokens stop working too
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {operator_token}"})
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_password_over_72_bytes_rejected(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/register", json={"email": "long@test.com", "password": "x" * 80})
    assert resp.status_code == 422


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
