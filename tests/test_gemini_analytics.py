"""
Tests for GeminiService and AnalyticsService.
These cover the lowest-coverage modules identified by the CI report.

GeminiService tests are pure unit tests — no DB, no network.
AnalyticsService tests use the existing db_session fixture (SQLite in-memory).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.telemetry import Telemetry
from app.models.user import UserRole
from app.services.analytics_service import AnalyticsService
from app.services.gemini_service import GeminiService
from tests.conftest import make_device, make_org, make_user

# ─────────────────────────────────────────────────────────────────────────────
# Shared test data
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_METRICS = {
    "top_contributors": [
        {
            "feature": "engine_temp",
            "z_score": 4.2,
            "value": 118.3,
            "train_mean": 85.0,
            "train_std": 5.0,
            "direction": "above",
        },
        {
            "feature": "vibration",
            "z_score": 3.1,
            "value": 8.5,
            "train_mean": 1.5,
            "train_std": 0.4,
            "direction": "above",
        },
    ],
    "ensemble": {
        "isolation_forest_score": -0.45,
        "lof_confirmed": True,
        "confidence": "HIGH",
        "n_features": 10,
        "n_train_samples": 95,
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# GeminiService — unit tests (no DB, no network)
# ═════════════════════════════════════════════════════════════════════════════


class TestGeminiServiceBuildPrompt:
    """_build_prompt is a pure function — test it directly."""

    def _svc(self) -> GeminiService:
        svc = GeminiService.__new__(GeminiService)
        svc._client = None
        return svc

    def test_prompt_contains_device_name(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
            fault_type="COOLANT_LEAK",
            fault_confidence="HIGH",
        )
        assert "Truck-Beta" in prompt
        assert "truck" in prompt

    def test_prompt_contains_fault_type(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
            fault_type="COOLANT_LEAK",
            fault_confidence="HIGH",
        )
        assert "COOLANT_LEAK" in prompt
        assert "HIGH" in prompt

    def test_prompt_contains_sensor_values(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
        )
        assert "engine temp" in prompt.lower()
        assert "4.2" in prompt  # z-score

    def test_prompt_lof_confirmation_line(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,  # lof_confirmed=True
        )
        assert "Local Outlier Factor" in prompt

    def test_prompt_unknown_anomaly_fallback(self) -> None:
        """When no fault_type given it defaults to UNKNOWN_ANOMALY playbook."""
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="van",
            device_name="Van-01",
            anomaly_score=-0.12,
            affected_metrics={"top_contributors": [], "ensemble": {}},
        )
        assert "UNKNOWN" in prompt
        assert "Van-01" in prompt

    def test_prompt_score_severity_critical(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="T",
            anomaly_score=-0.2,  # below the critical threshold (-0.15)
            affected_metrics={"top_contributors": [], "ensemble": {}},
        )
        assert "CRITICAL" in prompt

    def test_prompt_score_severity_medium(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="T",
            anomaly_score=-0.10,  # between the MEDIUM (-0.08) and CRITICAL (-0.15) thresholds
            affected_metrics={"top_contributors": [], "ensemble": {}},
        )
        assert "MEDIUM" in prompt

    def test_prompt_different_fault_types(self) -> None:
        svc = self._svc()
        for fault in ["BATTERY_FAILURE", "TRANSMISSION_STRESS", "BRAKE_WEAR", "ENGINE_STRESS",
                      "WHEEL_BEARING", "LOW_OIL_PRESSURE", "TIRE_PRESSURE"]:
            prompt = svc._build_prompt(
                device_type="truck",
                device_name="T",
                anomaly_score=-0.35,
                affected_metrics={"top_contributors": [], "ensemble": {}},
                fault_type=fault,
                fault_confidence="MEDIUM",
            )
            assert fault in prompt


class TestGeminiServiceFallback:
    """_fallback_summary produces a rule-based string without any API call."""

    def _svc(self) -> GeminiService:
        svc = GeminiService.__new__(GeminiService)
        svc._client = None
        return svc

    def test_fallback_no_contributors(self) -> None:
        svc = self._svc()
        result = svc._fallback_summary("truck", {}, "COOLANT_LEAK", "HIGH")
        assert "truck" in result
        assert "COOLANT_LEAK" in result
        assert len(result) > 20

    def test_fallback_with_contributors(self) -> None:
        svc = self._svc()
        result = svc._fallback_summary(
            "truck",
            _SAMPLE_METRICS,
            "COOLANT_LEAK",
            "HIGH",
        )
        assert "engine temp" in result.lower()
        assert "COOLANT_LEAK" in result

    def test_fallback_unknown_anomaly(self) -> None:
        svc = self._svc()
        result = svc._fallback_summary("van", {}, None, None)
        assert "van" in result
        assert isinstance(result, str)

    def test_fallback_battery_failure(self) -> None:
        svc = self._svc()
        result = svc._fallback_summary("truck", _SAMPLE_METRICS, "BATTERY_FAILURE", "MEDIUM")
        assert "BATTERY_FAILURE" in result


class TestGeminiServiceGenerate:
    """generate_alert_summary — test both paths (no key / mocked key)."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_fallback(self) -> None:
        """With no Gemini key the fallback string is returned (never None)."""
        svc = GeminiService.__new__(GeminiService)
        svc._client = None
        result = await svc.generate_alert_summary(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
            fault_type="COOLANT_LEAK",
            fault_confidence="HIGH",
        )
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 10

    @staticmethod
    def _mock_client(text: str | None = None, error: Exception | None = None) -> MagicMock:
        client = MagicMock()
        if error is not None:
            client.aio.models.generate_content = AsyncMock(side_effect=error)
        else:
            resp = MagicMock()
            resp.text = text
            client.aio.models.generate_content = AsyncMock(return_value=resp)
        return client

    def _svc_with(self, client: MagicMock) -> GeminiService:
        svc = GeminiService.__new__(GeminiService)
        svc._client = client
        svc._model = "gemini-flash-latest"
        return svc

    @pytest.mark.asyncio
    async def test_mocked_client_returns_response_text(self) -> None:
        """When _client is set, the async generate_content is called and text is returned."""
        mock_client = self._mock_client("Truck-Beta shows COOLANT_LEAK with HIGH confidence.")
        svc = self._svc_with(mock_client)

        result = await svc.generate_alert_summary(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
            fault_type="COOLANT_LEAK",
            fault_confidence="HIGH",
        )
        assert result == "Truck-Beta shows COOLANT_LEAK with HIGH confidence."
        assert mock_client.aio.models.generate_content.await_count == 1
        assert mock_client.aio.models.generate_content.call_args.kwargs["model"] == "gemini-flash-latest"

    @pytest.mark.asyncio
    async def test_empty_response_falls_back(self) -> None:
        svc = self._svc_with(self._mock_client(""))
        result = await svc.generate_alert_summary(
            device_type="truck", device_name="T", anomaly_score=-0.4,
            affected_metrics=_SAMPLE_METRICS, fault_type="COOLANT_LEAK", fault_confidence="HIGH",
        )
        assert "COOLANT_LEAK" in result

    @pytest.mark.asyncio
    async def test_mocked_client_exception_falls_back(self) -> None:
        """If generate_content raises, the fallback summary is returned."""
        svc = self._svc_with(self._mock_client(error=RuntimeError("API error")))

        result = await svc.generate_alert_summary(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
        )
        assert result is not None
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_prompt_is_passed_to_client(self) -> None:
        """The prompt built by _build_prompt is forwarded to generate_content."""
        mock_client = self._mock_client("response")
        svc = self._svc_with(mock_client)

        await svc.generate_alert_summary(
            device_type="truck",
            device_name="Truck-Alpha",
            anomaly_score=-0.55,
            affected_metrics={"top_contributors": [], "ensemble": {}},
            fault_type="ENGINE_STRESS",
            fault_confidence="HIGH",
        )
        prompt_sent = mock_client.aio.models.generate_content.call_args.kwargs["contents"]
        assert "Truck-Alpha" in prompt_sent
        assert "ENGINE_STRESS" in prompt_sent


# ═════════════════════════════════════════════════════════════════════════════
# AnalyticsService — integration tests (SQLite in-memory via db_session)
# ═════════════════════════════════════════════════════════════════════════════


async def _org_admin(db: AsyncSession, email: str) -> tuple:
    org = await make_org(db, email)
    return org, await make_user(db, org, email, UserRole.ADMIN)


async def _make_telemetry(db: AsyncSession, device: Device, offset_s: int = 0) -> Telemetry:
    from datetime import timedelta
    record = Telemetry(
        id=uuid.uuid4(),
        device_id=device.id,
        recorded_at=datetime.now(timezone.utc) - timedelta(seconds=offset_s),
        gps_lat=37.0,
        gps_lon=-122.0,
        engine_temp=87.0,
        rpm=1500.0,
        fuel_level=60.0,
        battery_voltage=13.2,
        speed=55.0,
        vibration=1.5,
    )
    db.add(record)
    await db.flush()
    return record


@pytest.mark.asyncio
async def test_fleet_summary_empty(db_session: AsyncSession) -> None:
    """An organization with no devices gets a zero-filled response."""
    _, user = await _org_admin(db_session, "empty@test.com")
    result = await AnalyticsService(db_session).fleet_summary(user)
    assert result["total_devices"] == 0
    assert result["active_devices"] == 0
    assert result["total_alerts"] == 0
    assert result["unacknowledged_alerts"] == 0
    assert result["alerts_by_severity"] == {}


@pytest.mark.asyncio
async def test_fleet_summary_is_scoped_to_organization(db_session: AsyncSession) -> None:
    """Users only ever see their own organization's vehicles."""
    org_a, user_a = await _org_admin(db_session, "a@test.com")
    org_b, _ = await _org_admin(db_session, "b@test.com")
    await make_device(db_session, org_a, name="Mine")
    await make_device(db_session, org_b, name="Theirs")
    await make_device(db_session, org_b, name="Theirs 2")
    result = await AnalyticsService(db_session).fleet_summary(user_a)
    assert result["total_devices"] == 1


@pytest.mark.asyncio
async def test_fleet_summary_all_roles_see_whole_org(db_session: AsyncSession) -> None:
    org, _ = await _org_admin(db_session, "admin_a@test.com")
    viewer = await make_user(db_session, org, "viewer_a@test.com", UserRole.VIEWER)
    await make_device(db_session, org, name="T1")
    await make_device(db_session, org, name="T2")
    result = await AnalyticsService(db_session).fleet_summary(viewer)
    assert result["total_devices"] == 2


@pytest.mark.asyncio
async def test_fleet_summary_active_count(db_session: AsyncSession) -> None:
    org, admin = await _org_admin(db_session, "admin_b@test.com")
    await make_device(db_session, org, name="Active")
    inactive = await make_device(db_session, org, name="Inactive")
    inactive.is_active = False
    await db_session.flush()
    result = await AnalyticsService(db_session).fleet_summary(admin)
    assert result["active_devices"] < result["total_devices"]


@pytest.mark.asyncio
async def test_fleet_summary_with_telemetry_averages(db_session: AsyncSession) -> None:
    org, admin = await _org_admin(db_session, "admin_c@test.com")
    dev = await make_device(db_session, org, name="TelTruck")
    await _make_telemetry(db_session, dev)
    result = await AnalyticsService(db_session).fleet_summary(admin)
    assert result["avg_engine_temp"] is not None
    assert result["avg_fuel_level"] is not None
    assert 0 < result["avg_engine_temp"] < 200


@pytest.mark.asyncio
async def test_device_trends_not_found(db_session: AsyncSession) -> None:
    from app.core.exceptions import NotFoundError

    _, admin = await _org_admin(db_session, "admin_d@test.com")
    with pytest.raises(NotFoundError):
        await AnalyticsService(db_session).device_trends(uuid.uuid4(), admin)


@pytest.mark.asyncio
async def test_device_trends_other_org_not_found(db_session: AsyncSession) -> None:
    """Cross-tenant access is a 404, not a 403 — don't confirm the device exists."""
    from app.core.exceptions import NotFoundError

    org_a, _ = await _org_admin(db_session, "owner@test.com")
    _, stranger = await _org_admin(db_session, "stranger@test.com")
    dev = await make_device(db_session, org_a, name="Owned Truck")
    with pytest.raises(NotFoundError):
        await AnalyticsService(db_session).device_trends(dev.id, stranger)


@pytest.mark.asyncio
async def test_device_trends_no_telemetry(db_session: AsyncSession) -> None:
    org, admin = await _org_admin(db_session, "admin_e@test.com")
    dev = await make_device(db_session, org, name="Empty Truck")
    result = await AnalyticsService(db_session).device_trends(dev.id, admin)
    assert result["sample_count"] == 0
    assert result["trends"] == {}
    assert result["device_id"] == str(dev.id)


@pytest.mark.asyncio
async def test_device_trends_with_telemetry(db_session: AsyncSession) -> None:
    org, admin = await _org_admin(db_session, "admin_f@test.com")
    dev = await make_device(db_session, org, name="Data Truck")
    await _make_telemetry(db_session, dev, 2)
    await _make_telemetry(db_session, dev, 0)
    result = await AnalyticsService(db_session).device_trends(dev.id, admin)
    assert result["sample_count"] == 2
    trend = result["trends"]["engine_temp"]
    assert trend["min"] <= trend["avg"] <= trend["max"]
    assert len(result["series"]) == 2


@pytest.mark.asyncio
async def test_alert_timeline_shape(db_session: AsyncSession) -> None:
    _, admin = await _org_admin(db_session, "admin_g@test.com")
    rows = await AnalyticsService(db_session).alert_timeline(admin, days=7)
    assert len(rows) == 8
    assert set(rows[0]) == {"date", "LOW", "MEDIUM", "CRITICAL"}
