"""Work orders close the loop on alerts; schedules drive preventive maintenance."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertSeverity, FaultConfidence, FaultType
from tests.conftest import auth, create_member


async def _alert(db: AsyncSession, device_id: str) -> Alert:
    alert = Alert(
        id=uuid.uuid4(), device_id=uuid.UUID(device_id), severity=AlertSeverity.CRITICAL,
        anomaly_score=-0.6, affected_metrics={}, fault_type=FaultType.COOLANT_LEAK,
        fault_confidence=FaultConfidence.HIGH, llm_summary="Coolant hose leaking.",
    )
    db.add(alert)
    await db.flush()
    return alert


@pytest.mark.asyncio
async def test_work_order_from_alert(client: AsyncClient, admin_token: str, test_device: dict, db_session) -> None:
    alert = await _alert(db_session, test_device["id"])
    resp = await client.post("/api/v1/maintenance/work-orders", json={"alert_id": str(alert.id), "priority": "URGENT"},
                             headers=auth(admin_token))
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert wo["number"] == 1
    assert wo["device_id"] == test_device["id"]
    assert wo["title"] == "Coolant Leak — Test Truck"
    assert wo["description"] == "Coolant hose leaking."
    # Raising a work order acknowledges the alert
    a = (await client.get(f"/api/v1/alerts/{alert.id}", headers=auth(admin_token))).json()
    assert a["acknowledged"] is True
    assert a["work_order_id"] == wo["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("root_cause,expected", [("COOLANT_LEAK", "TRUE_POSITIVE"), ("NO_FAULT_FOUND", "FALSE_POSITIVE")])
async def test_resolving_labels_the_alert(
    client: AsyncClient, admin_token: str, test_device: dict, db_session, root_cause: str, expected: str
) -> None:
    alert = await _alert(db_session, test_device["id"])
    wo = (await client.post("/api/v1/maintenance/work-orders", json={"alert_id": str(alert.id)},
                            headers=auth(admin_token))).json()
    url = f"/api/v1/maintenance/work-orders/{wo['id']}"

    no_cause = await client.patch(url, json={"status": "RESOLVED"}, headers=auth(admin_token))
    assert no_cause.status_code == 400

    resolved = await client.patch(url, json={"status": "RESOLVED", "root_cause": root_cause,
                                             "parts_cost": 120, "labor_cost": 80, "downtime_hours": 2},
                                  headers=auth(admin_token))
    assert resolved.status_code == 200
    assert resolved.json()["total_cost"] == 200
    assert resolved.json()["resolved_at"]

    a = (await client.get(f"/api/v1/alerts/{alert.id}", headers=auth(admin_token))).json()
    assert a["feedback"] == expected


@pytest.mark.asyncio
async def test_work_order_permissions(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    viewer = await create_member(client, admin_token, "v@test.com", "VIEWER")
    tech = await create_member(client, admin_token, "t@test.com", "TECHNICIAN")
    body = {"device_id": test_device["id"], "title": "Brake check"}
    assert (await client.post("/api/v1/maintenance/work-orders", json=body, headers=auth(viewer))).status_code == 403
    wo = (await client.post("/api/v1/maintenance/work-orders", json=body, headers=auth(tech))).json()
    upd = await client.patch(f"/api/v1/maintenance/work-orders/{wo['id']}", json={"status": "IN_PROGRESS"},
                             headers=auth(tech))
    assert upd.status_code == 200
    assert upd.json()["started_at"]
    # Deleting needs MANAGER+
    assert (await client.delete(f"/api/v1/maintenance/work-orders/{wo['id']}", headers=auth(tech))).status_code == 403


@pytest.mark.asyncio
async def test_assign_to_user_in_other_org_rejected(
    client: AsyncClient, admin_token: str, other_org_token: str, test_device: dict
) -> None:
    other_id = (await client.get("/api/v1/auth/me", headers=auth(other_org_token))).json()["id"]
    resp = await client.post("/api/v1/maintenance/work-orders",
                             json={"device_id": test_device["id"], "assigned_to_id": other_id},
                             headers=auth(admin_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_work_order_list_filters(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    for title in ("A", "B"):
        await client.post("/api/v1/maintenance/work-orders", json={"device_id": test_device["id"], "title": title},
                          headers=auth(admin_token))
    open_any = (await client.get("/api/v1/maintenance/work-orders?status=OPEN_ANY", headers=auth(admin_token))).json()
    assert open_any["total"] == 2
    resolved = (await client.get("/api/v1/maintenance/work-orders?status=RESOLVED", headers=auth(admin_token))).json()
    assert resolved["total"] == 0


@pytest.mark.asyncio
async def test_service_schedule_due(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    resp = await client.post("/api/v1/maintenance/schedules", json={
        "device_id": test_device["id"], "name": "Oil change", "interval_days": 180, "last_service_at": past,
    }, headers=auth(admin_token))
    assert resp.status_code == 201, resp.text
    s = resp.json()
    assert s["due"] is True
    assert s["days_remaining"] < 0

    ok = await client.post("/api/v1/maintenance/schedules", json={
        "device_id": test_device["id"], "name": "Tyres", "interval_km": 50000,
    }, headers=auth(admin_token))
    assert ok.json()["due"] is False
    assert ok.json()["km_remaining"] == 50000


@pytest.mark.asyncio
async def test_schedule_needs_an_interval(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    resp = await client.post("/api/v1/maintenance/schedules", json={"device_id": test_device["id"], "name": "x"},
                             headers=auth(admin_token))
    assert resp.status_code == 422
