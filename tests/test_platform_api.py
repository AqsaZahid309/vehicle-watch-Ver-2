"""Users/org admin, alerts workflow, notifications, reports, stream tickets and ops endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.alert import Alert, AlertSeverity, FaultType
from app.models.notification import NotificationChannel
from app.services.notification_service import DeliveryError, assert_public_url, channel_accepts
from app.services.report_service import cost_avoided_for
from tests.conftest import auth, create_member


async def _alert(db, device_id: str, severity=AlertSeverity.MEDIUM) -> Alert:
    a = Alert(id=uuid.uuid4(), device_id=uuid.UUID(device_id), severity=severity, anomaly_score=-0.35,
              affected_metrics={}, fault_type=FaultType.BATTERY_FAILURE)
    db.add(a)
    await db.flush()
    return a


# ── Users & organization ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_manages_team(client: AsyncClient, admin_token: str) -> None:
    tech = await create_member(client, admin_token, "tech@test.com", "TECHNICIAN")
    users = (await client.get("/api/v1/users", headers=auth(admin_token))).json()
    assert {u["email"] for u in users} == {"admin@test.com", "tech@test.com"}
    # non-admins cannot add users
    resp = await client.post("/api/v1/users", json={"email": "x@test.com", "password": "password123"},
                             headers=auth(tech))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_last_admin_cannot_be_demoted(client: AsyncClient, admin_token: str, admin_user_id: str) -> None:
    resp = await client.patch(f"/api/v1/users/{admin_user_id}", json={"role": "VIEWER"}, headers=auth(admin_token))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_organization_settings(client: AsyncClient, admin_token: str, operator_token: str) -> None:
    body = {"currency": "GBP", "fuel_price_per_liter": 1.42, "escalation_minutes": 10}
    assert (await client.patch("/api/v1/organization", json=body, headers=auth(operator_token))).status_code == 403
    resp = await client.patch("/api/v1/organization", json=body, headers=auth(admin_token))
    assert resp.json()["currency"] == "GBP"
    logs = (await client.get("/api/v1/audit-logs", headers=auth(admin_token))).json()
    assert any(entry["action"] == "organization.updated" for entry in logs)


# ── Alerts ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acknowledge_records_who_and_when(
    client: AsyncClient, operator_token: str, test_device: dict, db_session
) -> None:
    a = await _alert(db_session, test_device["id"])
    resp = await client.patch(f"/api/v1/alerts/{a.id}/acknowledge", headers=auth(operator_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["acknowledged"] is True
    assert body["acknowledged_at"] and body["acknowledged_by_id"]
    reopened = await client.patch(f"/api/v1/alerts/{a.id}/acknowledge", json={"acknowledged": False},
                                  headers=auth(operator_token))
    assert reopened.json()["acknowledged"] is False


@pytest.mark.asyncio
async def test_viewer_cannot_acknowledge(client: AsyncClient, viewer_token: str, test_device: dict, db_session) -> None:
    a = await _alert(db_session, test_device["id"])
    assert (await client.patch(f"/api/v1/alerts/{a.id}/acknowledge", headers=auth(viewer_token))).status_code == 403


@pytest.mark.asyncio
async def test_feedback_and_filters(client: AsyncClient, admin_token: str, test_device: dict, db_session) -> None:
    a = await _alert(db_session, test_device["id"])
    b = await _alert(db_session, test_device["id"], AlertSeverity.CRITICAL)
    fb = await client.post(f"/api/v1/alerts/{a.id}/feedback", json={"feedback": "FALSE_POSITIVE", "notes": "sensor"},
                           headers=auth(admin_token))
    assert fb.json()["feedback"] == "FALSE_POSITIVE"
    assert fb.json()["acknowledged"] is True
    fps = (await client.get("/api/v1/alerts?feedback=FALSE_POSITIVE", headers=auth(admin_token))).json()
    assert [x["id"] for x in fps["items"]] == [str(a.id)]
    crit = (await client.get("/api/v1/alerts?severity=CRITICAL", headers=auth(admin_token))).json()
    assert crit["items"][0]["device_name"] == "Test Truck"
    metrics = (await client.get("/api/v1/ml/metrics", headers=auth(admin_token))).json()
    assert metrics["labelled_alerts"] == 1
    assert metrics["precision"] == 0.0

    bulk = await client.post("/api/v1/alerts/acknowledge", json={"alert_ids": [str(b.id)]}, headers=auth(admin_token))
    assert bulk.json()["acknowledged"] == 1


@pytest.mark.asyncio
async def test_alerts_are_tenant_isolated(client: AsyncClient, other_org_token: str, test_device: dict, db_session) -> None:
    a = await _alert(db_session, test_device["id"])
    assert (await client.get(f"/api/v1/alerts/{a.id}", headers=auth(other_org_token))).status_code == 404
    assert (await client.get("/api/v1/alerts", headers=auth(other_org_token))).json()["total"] == 0


# ── Notifications ────────────────────────────────────────────────────────────

def test_channel_routing_rules() -> None:
    ch = NotificationChannel(enabled=True, event_types=["ALERT"], min_severity="MEDIUM")
    assert channel_accepts(ch, "ALERT", "CRITICAL")
    assert not channel_accepts(ch, "ALERT", "LOW")
    assert not channel_accepts(ch, "FUEL", "CRITICAL")
    ch.enabled = False
    assert not channel_accepts(ch, "ALERT", "CRITICAL")


@pytest.mark.asyncio
async def test_channel_crud_and_validation(client: AsyncClient, admin_token: str, operator_token: str) -> None:
    bad = await client.post("/api/v1/notifications/channels",
                            json={"name": "SMS", "channel_type": "SMS", "target": "0770090"}, headers=auth(admin_token))
    assert bad.status_code == 422
    body = {"name": "Ops Slack", "channel_type": "SLACK", "target": "https://hooks.slack.com/services/T/B/X",
            "min_severity": "CRITICAL", "event_types": ["ALERT", "ESCALATION"]}
    assert (await client.post("/api/v1/notifications/channels", json=body,
                              headers=auth(operator_token))).status_code == 403
    created = await client.post("/api/v1/notifications/channels", json=body, headers=auth(admin_token))
    assert created.status_code == 201
    cid = created.json()["id"]

    with patch("app.routers.notifications.send_via_channel", new=AsyncMock()) as sender:
        ok = await client.post(f"/api/v1/notifications/channels/{cid}/test", headers=auth(admin_token))
    assert ok.json() == {"status": "sent"}
    sender.assert_awaited_once()

    with patch("app.routers.notifications.send_via_channel", new=AsyncMock(side_effect=DeliveryError("HTTP 404"))):
        failed = await client.post(f"/api/v1/notifications/channels/{cid}/test", headers=auth(admin_token))
    assert failed.status_code == 502
    assert "HTTP 404" in failed.json()["detail"]


@pytest.mark.asyncio
async def test_in_app_notifications(client: AsyncClient, admin_token: str, test_device: dict, db_session) -> None:
    from app.services.notification_service import NotificationService
    me = (await client.get("/api/v1/auth/me", headers=auth(admin_token))).json()
    await NotificationService(db_session).create(uuid.UUID(me["organization_id"]), "ALERT", "Hot engine", "details",
                                                 severity="CRITICAL")
    feed = (await client.get("/api/v1/notifications", headers=auth(admin_token))).json()
    assert feed["unread"] == 1
    await client.post("/api/v1/notifications/read-all", headers=auth(admin_token))
    assert (await client.get("/api/v1/notifications", headers=auth(admin_token))).json()["unread"] == 0


def test_ssrf_guard_blocks_private_addresses(monkeypatch) -> None:
    from app.services import notification_service as ns
    monkeypatch.setattr(ns.settings, "app_env", "production")
    for url in ("https://127.0.0.1/hook", "https://169.254.169.254/latest/meta-data", "http://example.com/x"):
        with pytest.raises(DeliveryError):
            assert_public_url(url)


# ── Reports ──────────────────────────────────────────────────────────────────

def test_cost_avoided_model() -> None:
    assert cost_avoided_for("COOLANT_LEAK", 0) == 12000 - 200
    assert cost_avoided_for("COOLANT_LEAK", 500) == 11500
    assert cost_avoided_for("NO_FAULT_FOUND", 0) == 0


@pytest.mark.asyncio
async def test_report_summary_and_exports(client: AsyncClient, admin_token: str, test_device: dict, db_session) -> None:
    a = await _alert(db_session, test_device["id"])
    wo = (await client.post("/api/v1/maintenance/work-orders", json={"alert_id": str(a.id)},
                            headers=auth(admin_token))).json()
    await client.patch(f"/api/v1/maintenance/work-orders/{wo['id']}",
                       json={"status": "RESOLVED", "root_cause": "BATTERY_FAILURE", "parts_cost": 150},
                       headers=auth(admin_token))
    summary = (await client.get("/api/v1/reports/summary", headers=auth(admin_token))).json()
    assert summary["alerts"]["total"] == 1
    assert summary["maintenance"]["work_orders_resolved"] == 1
    assert summary["cost_avoided"]["total"] == 1500 - 150
    assert summary["alerts"]["precision"] == 1.0

    csv = await client.get("/api/v1/reports/export/alerts", headers=auth(admin_token))
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "BATTERY_FAILURE" in csv.text
    assert (await client.get("/api/v1/reports/export/nope", headers=auth(admin_token))).status_code == 404
    assert (await client.get("/api/v1/reports/export/telemetry", headers=auth(admin_token))).status_code == 400


# ── Fleet API smoke ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drivers_geofences_and_fuel_endpoints(client: AsyncClient, admin_token: str, test_device: dict) -> None:
    h = auth(admin_token)
    driver = (await client.post("/api/v1/drivers", json={"name": "Ana"}, headers=h)).json()
    upd = await client.patch(f"/api/v1/devices/{test_device['id']}", json={"assigned_driver_id": driver["id"]}, headers=h)
    assert upd.json()["assigned_driver_id"] == driver["id"]
    bad = await client.post("/api/v1/geofences", json={"name": "x", "shape": "CIRCLE"}, headers=h)
    assert bad.status_code == 422
    fence = await client.post("/api/v1/geofences", json={
        "name": "Depot", "kind": "DEPOT", "center_lat": 51.5, "center_lon": -0.1, "radius_m": 300}, headers=h)
    assert fence.status_code == 201
    assert (await client.get("/api/v1/geofences", headers=h)).json()[0]["vehicles_inside"] == 0
    assert (await client.get("/api/v1/trips", headers=h)).json()["total"] == 0
    assert (await client.get("/api/v1/drivers/scores", headers=h)).json() == []
    stats = (await client.get("/api/v1/fuel/stats", headers=h)).json()
    assert stats[0]["device_name"] == "Test Truck"
    fc = (await client.get(f"/api/v1/analytics/devices/{test_device['id']}/forecast", headers=h)).json()
    assert fc["overall_risk"] == "NONE"


# ── Stream & ops ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_ticket_is_single_use(client: AsyncClient, admin_token: str, mock_redis) -> None:
    ticket = (await client.post("/api/v1/stream/ticket", headers=auth(admin_token))).json()["ticket"]
    key = f"vw:stream:ticket:{ticket}"
    assert key in mock_redis.store
    assert await mock_redis.getdel(key)          # consumed once...
    assert (await client.get(f"/api/v1/stream?ticket={ticket}")).status_code == 401   # ...never twice


@pytest.mark.asyncio
async def test_health_metrics_and_security_headers(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.json()["status"] == "ok"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in resp.headers
    metrics = await client.get("/metrics")
    assert "vw_http_requests_total" in metrics.text


@pytest.mark.asyncio
async def test_unknown_api_route_is_json_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404


def test_every_db_dependency_commits_before_the_response() -> None:
    """
    Regression guard: `get_db` commits after `yield`. With FastAPI's default
    ("request") dependency scope that commit runs after the response is sent, so
    a client could see 201 for a row that isn't visible yet. Every route must use
    `Depends(get_db, scope="function")`.
    """
    from fastapi.routing import APIRoute

    from app.database import get_db
    from app.main import app

    offenders: list[str] = []

    def walk(dependant, path: str) -> None:
        for dep in dependant.dependencies:
            if dep.call is get_db and getattr(dep, "scope", None) != "function":
                offenders.append(path)
            walk(dep, path)

    for route in app.routes:
        if isinstance(route, APIRoute):
            walk(route.dependant, f"{sorted(route.methods)} {route.path}")
    assert not offenders, offenders
