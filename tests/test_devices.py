import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_device_as_admin(
    client: AsyncClient, admin_token: str, admin_user_id: str
) -> None:
    resp = await client.post(
        "/api/v1/devices",
        json={"name": "Truck Alpha", "device_type": "truck", "owner_id": admin_user_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Truck Alpha"
    assert data["device_type"] == "truck"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_device_as_operator_forbidden(
    client: AsyncClient, operator_token: str, admin_user_id: str
) -> None:
    resp = await client.post(
        "/api/v1/devices",
        json={"name": "Truck Beta", "device_type": "truck", "owner_id": admin_user_id},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_device_unauthenticated(client: AsyncClient, admin_user_id: str) -> None:
    resp = await client.post(
        "/api/v1/devices",
        json={"name": "Truck Gamma", "device_type": "truck", "owner_id": admin_user_id},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_devices(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    resp = await client.get(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert any(d["id"] == test_device["id"] for d in items)


@pytest.mark.asyncio
async def test_get_device_by_id(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]
    resp = await client.get(
        f"/api/v1/devices/{device_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == device_id


@pytest.mark.asyncio
async def test_get_nonexistent_device(client: AsyncClient, admin_token: str) -> None:
    import uuid
    resp = await client.get(
        f"/api/v1/devices/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_device_status_no_cache(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]
    resp = await client.get(
        f"/api/v1/devices/{device_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is False
    assert data["latest_telemetry"] is None


@pytest.mark.asyncio
async def test_delete_device_as_admin(
    client: AsyncClient, admin_token: str, admin_user_id: str
) -> None:
    create_resp = await client.post(
        "/api/v1/devices",
        json={"name": "To Delete", "device_type": "van", "owner_id": admin_user_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    device_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/api/v1/devices/{device_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_resp.status_code == 204

    get_resp = await client.get(
        f"/api/v1/devices/{device_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_create_device_defaults_owner_to_creator(client: AsyncClient, admin_token: str, admin_user_id: str) -> None:
    resp = await client.post(
        "/api/v1/devices",
        json={"name": "Van 7", "device_type": "van", "license_plate": "AB12 CDE", "fuel_tank_liters": 80},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["owner_id"] == admin_user_id
    assert data["license_plate"] == "AB12 CDE"
    assert data["fuel_tank_liters"] == 80


@pytest.mark.asyncio
async def test_update_device(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    resp = await client.patch(
        f"/api/v1/devices/{test_device['id']}",
        json={"make": "Volvo", "model": "FH16", "year": 2022},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["make"] == "Volvo"


@pytest.mark.asyncio
async def test_device_in_other_org_is_not_found(
    client: AsyncClient, other_org_token: str, test_device: dict
) -> None:
    """Tenant isolation: another organization gets 404 (not 403, which would leak existence)."""
    h = {"Authorization": f"Bearer {other_org_token}"}
    assert (await client.get(f"/api/v1/devices/{test_device['id']}", headers=h)).status_code == 404
    assert (await client.get(f"/api/v1/devices/{test_device['id']}/telemetry", headers=h)).status_code == 404
    assert (await client.get("/api/v1/devices", headers=h)).json() == []


@pytest.mark.asyncio
async def test_live_endpoint(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    resp = await client.get("/api/v1/devices/live", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    [row] = resp.json()
    assert row["id"] == test_device["id"]
    assert row["online"] is False
    assert row["health_score"] == 100
