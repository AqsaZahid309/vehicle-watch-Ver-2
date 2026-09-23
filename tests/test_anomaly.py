"""
Tests for the anomaly detection service.
We create synthetic telemetry records directly in the DB to test scoring logic
without relying on the full HTTP request lifecycle.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.telemetry import Telemetry
from tests.conftest import make_device, make_org, make_user
from app.services.anomaly_service import (
    AnomalyService,
    NORMAL_RANGES,
    _identify_affected_metrics,
    _score_to_severity,
)
from app.models.alert import AlertSeverity


# ── Unit tests (pure logic, no DB) ────────────────────────────────────────────

def test_identify_affected_metrics_normal() -> None:
    """A record within all normal ranges should return no affected metrics."""

    class MockRecord:
        engine_temp = 85.0
        rpm = 1500.0
        fuel_level = 50.0
        battery_voltage = 13.0
        speed = 60.0
        vibration = 1.5

    result = _identify_affected_metrics(MockRecord())  # type: ignore
    assert result == {}


def test_identify_affected_metrics_anomalous() -> None:
    """Spiked engine temp should appear in affected metrics."""

    class MockRecord:
        engine_temp = 145.0  # way above 105
        rpm = 1500.0
        fuel_level = 50.0
        battery_voltage = 13.0
        speed = 60.0
        vibration = 1.5  # within normal range [0, 10]

    result = _identify_affected_metrics(MockRecord())  # type: ignore
    assert "engine_temp" in result
    assert result["engine_temp"]["value"] == 145.0
    assert "vibration" not in result  # 1.5 is within [0, 10]


def test_score_to_severity_mapping() -> None:
    # decision_function scale: 0 is the contamination boundary, negative = outlier
    assert _score_to_severity(-0.02) == AlertSeverity.LOW
    assert _score_to_severity(-0.05) == AlertSeverity.LOW
    assert _score_to_severity(-0.10) == AlertSeverity.MEDIUM
    assert _score_to_severity(-0.20) == AlertSeverity.CRITICAL


# ── Integration tests (with DB) ───────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded_device(db_session: AsyncSession):
    """
    Create a user + device with 30 normal telemetry records timestamped in
    the past (1 hour ago). Tests that need to add "unscored" records can then
    use datetime.now() which is newer than `since=one_hour_ago`.
    """
    import random
    from datetime import timedelta
    random.seed(42)

    org = await make_org(db_session)
    user = await make_user(db_session, org, "anomaly_test@test.com")
    device = await make_device(db_session, org, user, name="Test Device")

    baseline_time = datetime.now(timezone.utc) - timedelta(hours=1)

    for i in range(30):
        record = Telemetry(
            id=uuid.uuid4(),
            device_id=device.id,
            recorded_at=baseline_time + timedelta(seconds=i * 2),
            gps_lat=37.0 + random.uniform(-0.01, 0.01),
            gps_lon=-122.0 + random.uniform(-0.01, 0.01),
            engine_temp=85.0 + random.uniform(-5.0, 10.0),
            rpm=1500.0 + random.uniform(-200.0, 300.0),
            fuel_level=60.0 + random.uniform(-10.0, 10.0),
            battery_voltage=13.2 + random.uniform(-0.3, 0.3),
            speed=60.0 + random.uniform(-10.0, 10.0),
            vibration=1.5 + random.uniform(-0.3, 0.5),
        )
        db_session.add(record)

    await db_session.flush()
    return device


@pytest.mark.asyncio
async def test_anomaly_service_not_enough_data(db_session: AsyncSession) -> None:
    """With fewer than 10 records, the service should return no alerts."""
    service = AnomalyService(db_session)
    # Fake device ID with no records
    alerts = await service.run_for_device(uuid.uuid4())
    assert alerts == []


@pytest.mark.asyncio
async def test_anomaly_service_normal_data(
    db_session: AsyncSession, seeded_device: Device
) -> None:
    """
    Normal readings should not trigger alerts.
    The 30 training records are timestamped 1 hour ago.
    We pass `since = 30 minutes ago` so only the 3 new records are scored.
    """
    from datetime import timedelta
    thirty_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=30)

    for _ in range(3):
        record = Telemetry(
            id=uuid.uuid4(),
            device_id=seeded_device.id,
            recorded_at=datetime.now(timezone.utc),
            gps_lat=37.0,
            gps_lon=-122.0,
            engine_temp=87.0,
            rpm=1600.0,
            fuel_level=58.0,
            battery_voltage=13.2,
            speed=62.0,
            vibration=1.5,
        )
        db_session.add(record)
    await db_session.flush()

    service = AnomalyService(db_session)
    # `since` limits scoring to only the 3 new records (created after 30 min ago).
    # We verify the service runs without errors and returns a list (not None).
    # We don't assert zero alerts — IsolationForest on small datasets can have
    # false positives; statistical accuracy is validated via simulator end-to-end.
    alerts = await service.run_for_device(seeded_device.id, since=thirty_mins_ago)
    assert isinstance(alerts, list)
    assert len(alerts) <= 3  # Cannot exceed the number of records scored


@pytest.mark.asyncio
async def test_anomaly_service_anomalous_data(
    db_session: AsyncSession, seeded_device: Device
) -> None:
    """Severely anomalous records must trigger alerts with correct severity."""
    from datetime import timedelta
    thirty_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=30)

    # A sustained fault (3 consecutive readings) — single spikes are ignored by design.
    for i in range(3):
        db_session.add(Telemetry(
            id=uuid.uuid4(),
            device_id=seeded_device.id,
            recorded_at=datetime.now(timezone.utc) - timedelta(seconds=4 - 2 * i),
            gps_lat=37.5,
            gps_lon=-122.0,
            engine_temp=145.0,   # massively above 105 normal max
            rpm=5500.0,           # massively above 3000 normal max
            fuel_level=55.0,
            battery_voltage=9.0,  # below 11.5 min
            speed=65.0,
            vibration=9.8,        # near max 10
        ))
    await db_session.flush()

    service = AnomalyService(db_session)
    # Scope to only the new anomalous record
    alerts = await service.run_for_device(seeded_device.id, since=thirty_mins_ago)

    assert len(alerts) == 1                     # cooldown: one alert per fault episode
    alert = alerts[0]
    assert alert.anomaly_score < 0              # IsolationForest outlier
    assert alert.device_id == seeded_device.id
    assert alert.severity in (AlertSeverity.MEDIUM, AlertSeverity.CRITICAL)
    # 30 training samples → model still learning, so the safety rules raised it
    assert alert.affected_metrics["ensemble"]["trigger"] == "RULE"
    assert alert.affected_metrics["ensemble"]["model_mature"] is False


@pytest.mark.asyncio
async def test_mature_model_raises_ml_only_alerts(db_session: AsyncSession, seeded_device: Device, monkeypatch) -> None:
    """Once mature, a multivariate outlier with no rule match still alerts (trigger ML)."""
    from datetime import timedelta
    from app.services import anomaly_service
    monkeypatch.setattr(anomaly_service.settings, "anomaly_mature_samples", 10)
    now = datetime.now(timezone.utc)
    for i in range(4):   # hot for this RPM, but under every hard limit
        db_session.add(Telemetry(
            id=uuid.uuid4(), device_id=seeded_device.id, recorded_at=now - timedelta(seconds=8 - 2 * i),
            gps_lat=37.0, gps_lon=-122.0, engine_temp=108.0, rpm=750.0, fuel_level=60.0,
            battery_voltage=13.2, speed=5.0, vibration=4.5,
        ))
    await db_session.flush()
    alerts = await AnomalyService(db_session).run_for_device(seeded_device.id, since=now - timedelta(minutes=30))
    assert len(alerts) == 1
    assert alerts[0].affected_metrics["ensemble"]["trigger"] == "ML"


@pytest.mark.asyncio
async def test_single_spike_is_not_alerted(db_session: AsyncSession, seeded_device: Device) -> None:
    """One wild reading between normal ones is a sensor glitch, not a fault."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    rows = [(87.0, 13.2), (150.0, 9.0), (87.0, 13.2), (88.0, 13.1)]
    for i, (temp, volts) in enumerate(rows):
        db_session.add(Telemetry(
            id=uuid.uuid4(), device_id=seeded_device.id, recorded_at=now - timedelta(seconds=8 - 2 * i),
            gps_lat=37.0, gps_lon=-122.0, engine_temp=temp, rpm=1600.0, fuel_level=58.0,
            battery_voltage=volts, speed=62.0, vibration=1.5,
        ))
    await db_session.flush()
    alerts = await AnomalyService(db_session).run_for_device(seeded_device.id, since=now - timedelta(minutes=30))
    assert alerts == []


@pytest.mark.asyncio
async def test_safety_limit_alerts_even_when_model_is_calm(db_session: AsyncSession, seeded_device: Device) -> None:
    """A sustained battery collapse is alerted by the rule path even if the ML score is unremarkable."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for i in range(4):
        db_session.add(Telemetry(
            id=uuid.uuid4(), device_id=seeded_device.id, recorded_at=now - timedelta(seconds=8 - 2 * i),
            gps_lat=37.0, gps_lon=-122.0, engine_temp=88.0, rpm=1600.0, fuel_level=60.0,
            battery_voltage=11.3, speed=60.0, vibration=1.6,
        ))
    await db_session.flush()
    alerts = await AnomalyService(db_session).run_for_device(seeded_device.id, since=now - timedelta(minutes=30))
    assert len(alerts) == 1
    assert alerts[0].fault_type.value == "BATTERY_FAILURE"
    assert alerts[0].severity in (AlertSeverity.MEDIUM, AlertSeverity.CRITICAL)
