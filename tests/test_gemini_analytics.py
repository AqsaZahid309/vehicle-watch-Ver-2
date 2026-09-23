"""
Tests for GeminiService and AnalyticsService.
These cover the lowest-coverage modules identified by the CI report.

GeminiService tests are pure unit tests — no DB, no network.
AnalyticsService tests use the existing db_session fixture (SQLite in-memory).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.telemetry import Telemetry
from app.models.user import User, UserRole
from app.services.analytics_service import AnalyticsService
from app.services.gemini_service import GeminiService

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
            anomaly_score=-0.6,  # below critical threshold -0.5
            affected_metrics={"top_contributors": [], "ensemble": {}},
        )
        assert "CRITICAL" in prompt

    def test_prompt_score_severity_medium(self) -> None:
        svc = self._svc()
        prompt = svc._build_prompt(
            device_type="truck",
            device_name="T",
            anomaly_score=-0.35,  # between -0.3 and -0.5
            affected_metrics={"top_contributors": [], "ensemble": {}},
        )
        assert "MEDIUM" in prompt

    def test_prompt_different_fault_types(self) -> None:
        svc = self._svc()
        for fault in ["BATTERY_FAILURE", "TRANSMISSION_STRESS", "BRAKE_WEAR", "ENGINE_STRESS"]:
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

    @pytest.mark.asyncio
    async def test_mocked_client_returns_response_text(self) -> None:
        """When _client is set, generate_content is called and text is returned."""
        svc = GeminiService.__new__(GeminiService)
        mock_resp = MagicMock()
        mock_resp.text = "Truck-Beta shows COOLANT_LEAK with HIGH confidence."
        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_resp
        svc._client = mock_client

        result = await svc.generate_alert_summary(
            device_type="truck",
            device_name="Truck-Beta",
            anomaly_score=-0.45,
            affected_metrics=_SAMPLE_METRICS,
            fault_type="COOLANT_LEAK",
            fault_confidence="HIGH",
        )
        assert result == "Truck-Beta shows COOLANT_LEAK with HIGH confidence."
        assert mock_client.generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_mocked_client_exception_falls_back(self) -> None:
        """If generate_content raises, the fallback summary is returned."""
        svc = GeminiService.__new__(GeminiService)
        mock_client = MagicMock()
        mock_client.generate_content.side_effect = RuntimeError("API error")
        svc._client = mock_client

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
        svc = GeminiService.__new__(GeminiService)
        mock_resp = MagicMock()
        mock_resp.text = "response"
        mock_client = MagicMock()
        mock_client.generate_content.return_value = mock_resp
        svc._client = mock_client

        await svc.generate_alert_summary(
            device_type="truck",
            device_name="Truck-Alpha",
            anomaly_score=-0.55,
            affected_metrics={"top_contributors": [], "ensemble": {}},
            fault_type="ENGINE_STRESS",
            fault_confidence="HIGH",
        )
        call_args = mock_client.generate_content.call_args
        prompt_sent = call_args[0][0]
        assert "Truck-Alpha" in prompt_sent
        assert "ENGINE_STRESS" in prompt_sent


# ═════════════════════════════════════════════════════════════════════════════
# AnalyticsService — integration tests (SQLite in-memory via db_session)
# ═════════════════════════════════════════════════════════════════════════════


async def _make_user(db: AsyncSession, email: str, role: UserRole = UserRole.OPERATOR) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="hashed",
        role=role,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_device(db: AsyncSession, owner: User, name: str = "Test Truck") -> Device:
    device = Device(
        id=uuid.uuid4(),
        name=name,
        device_type="truck",
        owner_id=owner.id,
    )
    db.add(device)
    await db.flush()
    return device


async def _make_telemetry(db: AsyncSession, device: Device) -> Telemetry:
    record = Telemetry(
        id=uuid.uuid4(),
        device_id=device.id,
        recorded_at=datetime.now(timezone.utc),
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
    """Operator with no devices gets zero-filled response."""
    user = await _make_user(db_session, "empty@test.com")
    svc = AnalyticsService(db_session)
    result = await svc.fleet_summary(user)
    assert result["total_devices"] == 0
    assert result["active_devices"] == 0
    assert result["total_alerts"] == 0
    assert result["unacknowledged_alerts"] == 0
    assert result["alerts_by_severity"] == {}


@pytest.mark.asyncio
async def test_fleet_summary_operator_sees_own_devices(db_session: AsyncSession) -> None:
    """Operator only sees their own device."""
    op = await _make_user(db_session, "op@test.com", UserRole.OPERATOR)
    other = await _make_user(db_session, "other@test.com", UserRole.OPERATOR)
    await _make_device(db_session, op, "My Truck")
    await _make_device(db_session, other, "Other Truck")

    svc = AnalyticsService(db_session)
    result = await svc.fleet_summary(op)
    assert result["total_devices"] == 1


@pytest.mark.asyncio
async def test_fleet_summary_admin_sees_all_devices(db_session: AsyncSession) -> None:
    """Admin sees all devices regardless of owner."""
    admin = await _make_user(db_session, "admin_a@test.com", UserRole.ADMIN)
    op1 = await _make_user(db_session, "op1_a@test.com", UserRole.OPERATOR)
    op2 = await _make_user(db_session, "op2_a@test.com", UserRole.OPERATOR)
    await _make_device(db_session, op1, "T1")
    await _make_device(db_session, op2, "T2")

    svc = AnalyticsService(db_session)
    result = await svc.fleet_summary(admin)
    assert result["total_devices"] >= 2


@pytest.mark.asyncio
async def test_fleet_summary_active_count(db_session: AsyncSession) -> None:
    """active_devices counts only devices with is_active=True."""
    admin = await _make_user(db_session, "admin_b@test.com", UserRole.ADMIN)
    dev_active = await _make_device(db_session, admin, "Active")
    dev_inactive = await _make_device(db_session, admin, "Inactive")
    dev_inactive.is_active = False
    await db_session.flush()

    svc = AnalyticsService(db_session)
    result = await svc.fleet_summary(admin)
    # At least one active device (dev_active) and one inactive (dev_inactive)
    assert result["active_devices"] < result["total_devices"]


@pytest.mark.asyncio
async def test_fleet_summary_with_telemetry_averages(db_session: AsyncSession) -> None:
    """avg_engine_temp and avg_fuel_level are computed from telemetry."""
    admin = await _make_user(db_session, "admin_c@test.com", UserRole.ADMIN)
    dev = await _make_device(db_session, admin, "TelTruck")
    await _make_telemetry(db_session, dev)

    svc = AnalyticsService(db_session)
    result = await svc.fleet_summary(admin)
    assert result["avg_engine_temp"] is not None
    assert result["avg_fuel_level"] is not None
    assert 0 < result["avg_engine_temp"] < 200


@pytest.mark.asyncio
async def test_device_trends_not_found(db_session: AsyncSession) -> None:
    """Requesting trends for a non-existent device raises NotFoundError."""
    from app.core.exceptions import NotFoundError

    admin = await _make_user(db_session, "admin_d@test.com", UserRole.ADMIN)
    svc = AnalyticsService(db_session)
    with pytest.raises(NotFoundError):
        await svc.device_trends(uuid.uuid4(), admin)


@pytest.mark.asyncio
async def test_device_trends_forbidden(db_session: AsyncSession) -> None:
    """An operator cannot view another user's device trends."""
    from app.core.exceptions import ForbiddenError

    owner = await _make_user(db_session, "owner@test.com", UserRole.OPERATOR)
    stranger = await _make_user(db_session, "stranger@test.com", UserRole.OPERATOR)
    dev = await _make_device(db_session, owner, "Owned Truck")

    svc = AnalyticsService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.device_trends(dev.id, stranger)


@pytest.mark.asyncio
async def test_device_trends_no_telemetry(db_session: AsyncSession) -> None:
    """Device trends with no telemetry returns empty trends dict."""
    admin = await _make_user(db_session, "admin_e@test.com", UserRole.ADMIN)
    dev = await _make_device(db_session, admin, "Empty Truck")

    svc = AnalyticsService(db_session)
    result = await svc.device_trends(dev.id, admin)
    assert result["sample_count"] == 0
    assert result["trends"] == {}
    assert result["device_id"] == str(dev.id)


@pytest.mark.asyncio
async def test_device_trends_with_telemetry(db_session: AsyncSession) -> None:
    """Device trends with data returns min/max/avg/latest per field."""
    admin = await _make_user(db_session, "admin_f@test.com", UserRole.ADMIN)
    dev = await _make_device(db_session, admin, "Data Truck")
    await _make_telemetry(db_session, dev)
    await _make_telemetry(db_session, dev)

    svc = AnalyticsService(db_session)
    result = await svc.device_trends(dev.id, admin)
    assert result["sample_count"] == 2
    assert "engine_temp" in result["trends"]
    trend = result["trends"]["engine_temp"]
    assert "min" in trend
    assert "max" in trend
    assert "avg" in trend
    assert "latest" in trend
    assert trend["min"] <= trend["avg"] <= trend["max"]
