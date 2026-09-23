"""Device gateway: API keys, batch uploads, client timestamps and idempotency."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from tests.conftest import auth

READING = {
    "gps_lat": 51.5074, "gps_lon": -0.1278, "engine_temp": 88.0, "rpm": 1500.0,
    "fuel_level": 60.0, "battery_voltage": 13.4, "speed": 50.0, "vibration": 1.2,
}


async def _key(client: AsyncClient, token: str, device_id: str) -> str:
    resp = await client.post(f"/api/v1/devices/{device_id}/api-key", headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_key"].startswith(f"vw_{body['api_key_prefix']}_")
    return body["api_key"]


@pytest.mark.asyncio
async def test_device_key_ingest(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    key = await _key(client, admin_token, test_device["id"])
    resp = await client.post("/api/v1/ingest/telemetry", json=READING, headers={"X-Device-Key": key})
    assert resp.status_code == 201
    assert resp.json()["device_id"] == test_device["id"]


@pytest.mark.asyncio
async def test_invalid_and_rotated_keys_rejected(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    old = await _key(client, admin_token, test_device["id"])
    new = await _key(client, admin_token, test_device["id"])
    assert (await client.post("/api/v1/ingest/telemetry", json=READING,
                              headers={"X-Device-Key": old})).status_code == 401
    assert (await client.post("/api/v1/ingest/telemetry", json=READING,
                              headers={"X-Device-Key": "garbage"})).status_code == 401
    assert (await client.post("/api/v1/ingest/telemetry", json=READING,
                              headers={"X-Device-Key": new})).status_code == 201


@pytest.mark.asyncio
async def test_revoked_key_rejected(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    key = await _key(client, admin_token, test_device["id"])
    assert (await client.delete(f"/api/v1/devices/{test_device['id']}/api-key",
                                headers=auth(admin_token))).status_code == 204
    resp = await client.post("/api/v1/ingest/telemetry", json=READING, headers={"X-Device-Key": key})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_key_cannot_write_to_another_device(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    other = (await client.post("/api/v1/devices", json={"name": "Other", "device_type": "truck"},
                               headers=auth(admin_token))).json()
    key = await _key(client, admin_token, test_device["id"])
    resp = await client.post(f"/api/v1/devices/{other['id']}/telemetry", json=READING,
                             headers={"X-Device-Key": key})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_without_key_route_requires_key(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/ingest/telemetry", json=READING)).status_code == 401


@pytest.mark.asyncio
async def test_batch_backfill_is_idempotent(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    key = await _key(client, admin_token, test_device["id"])
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    readings = [{**READING, "recorded_at": (base + timedelta(seconds=10 * i)).isoformat()} for i in range(20)]

    first = await client.post("/api/v1/ingest/telemetry/batch", json={"readings": readings},
                              headers={"X-Device-Key": key})
    assert first.status_code == 201
    assert first.json() == {"accepted": 20, "duplicates": 0}

    # Device never saw the response and re-sends the same buffer plus one new reading.
    extra = {**READING, "recorded_at": (base + timedelta(seconds=500)).isoformat()}
    again = await client.post("/api/v1/ingest/telemetry/batch", json={"readings": readings + [extra]},
                              headers={"X-Device-Key": key})
    assert again.json() == {"accepted": 1, "duplicates": 20}

    history = await client.get(f"/api/v1/devices/{test_device['id']}/telemetry?page_size=100",
                               headers=auth(admin_token))
    assert history.json()["total"] == 21
    # Backfilled rows keep their device timestamps (newest first)
    newest = datetime.fromisoformat(history.json()["items"][0]["recorded_at"])
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    assert abs((newest - (base + timedelta(seconds=500))).total_seconds()) < 1


@pytest.mark.asyncio
async def test_single_duplicate_timestamp_conflicts(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    url = f"/api/v1/devices/{test_device['id']}/telemetry"
    assert (await client.post(url, json={**READING, "recorded_at": ts}, headers=auth(admin_token))).status_code == 201
    assert (await client.post(url, json={**READING, "recorded_at": ts}, headers=auth(admin_token))).status_code == 409


@pytest.mark.asyncio
async def test_future_and_ancient_timestamps_rejected(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    url = f"/api/v1/devices/{test_device['id']}/telemetry"
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    ancient = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert (await client.post(url, json={**READING, "recorded_at": future}, headers=auth(admin_token))).status_code == 422
    assert (await client.post(url, json={**READING, "recorded_at": ancient}, headers=auth(admin_token))).status_code == 422


@pytest.mark.asyncio
async def test_extended_sensors_and_dtc_codes(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    resp = await client.post(
        f"/api/v1/devices/{test_device['id']}/telemetry",
        json={**READING, "oil_pressure": 42.0, "coolant_level": 90, "tire_pressure": 105,
              "ambient_temp": 18, "dtc_codes": [" p0217 ", "C0040"]},
        headers=auth(admin_token),
    )
    assert resp.status_code == 201
    assert resp.json()["dtc_codes"] == ["P0217", "C0040"]


@pytest.mark.asyncio
async def test_viewer_cannot_ingest(client: AsyncClient, viewer_token: str, test_device: dict) -> None:
    resp = await client.post(f"/api/v1/devices/{test_device['id']}/telemetry", json=READING,
                             headers=auth(viewer_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_publishes_live_event(client: AsyncClient, admin_token: str, test_device: dict, mock_redis) -> None:
    await client.post(f"/api/v1/devices/{test_device['id']}/telemetry", json=READING, headers=auth(admin_token))
    assert any('"telemetry"' in msg for _, msg in mock_redis.published)
    live = (await client.get("/api/v1/devices/live", headers=auth(admin_token))).json()
    assert live[0]["online"] is True
    assert live[0]["latest"]["speed"] == 50.0
