import pytest
from httpx import AsyncClient

VALID_TELEMETRY = {
    "gps_lat": 37.7749,
    "gps_lon": -122.4194,
    "engine_temp": 88.5,
    "rpm": 1500.0,
    "fuel_level": 65.0,
    "battery_voltage": 13.2,
    "speed": 60.0,
    "vibration": 1.5,
}


@pytest.mark.asyncio
async def test_ingest_telemetry_success(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]
    resp = await client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=VALID_TELEMETRY,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["device_id"] == device_id
    assert data["engine_temp"] == 88.5
    assert "id" in data
    assert "recorded_at" in data


@pytest.mark.asyncio
async def test_ingest_telemetry_invalid_lat(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]
    bad = {**VALID_TELEMETRY, "gps_lat": 200.0}  # out of range
    resp = await client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=bad,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_telemetry_invalid_fuel(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]
    bad = {**VALID_TELEMETRY, "fuel_level": 150.0}
    resp = await client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=bad,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_telemetry_unauthenticated(
    client: AsyncClient, test_device: dict
) -> None:
    resp = await client.post(
        f"/api/v1/devices/{test_device['id']}/telemetry",
        json=VALID_TELEMETRY,
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_telemetry_history(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]

    for _ in range(3):
        await client.post(
            f"/api/v1/devices/{device_id}/telemetry",
            json=VALID_TELEMETRY,
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    resp = await client.get(
        f"/api/v1/devices/{device_id}/telemetry",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_get_telemetry_pagination(
    client: AsyncClient, admin_token: str, test_device: dict
) -> None:
    device_id = test_device["id"]

    for _ in range(5):
        await client.post(
            f"/api/v1/devices/{device_id}/telemetry",
            json=VALID_TELEMETRY,
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    resp = await client.get(
        f"/api/v1/devices/{device_id}/telemetry?page=1&page_size=2",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["pages"] == 3


@pytest.mark.asyncio
async def test_telemetry_updates_redis_cache(
    client: AsyncClient, admin_token: str, test_device: dict, mock_redis
) -> None:
    device_id = test_device["id"]
    await client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=VALID_TELEMETRY,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # setex was called — Redis cache was updated
    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    assert f"device:{device_id}:latest" in call_args[0][0]
    assert call_args[0][1] == 300  # TTL
