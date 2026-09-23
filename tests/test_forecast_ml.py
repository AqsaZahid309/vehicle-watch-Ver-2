"""Failure forecasting, model registry, signed artifacts and fault classification."""

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import FaultConfidence, FaultType
from app.models.ml import ModelVersion
from app.models.telemetry import Telemetry
from app.services.anomaly_service import (
    AnomalyService, _deserialize, _extract_features, _serialize, _train, classify_dtc,
    fault_classifier, population_stability_index,
)
from app.services.forecast_service import SIGNALS, ForecastService, forecast_signal
from app.services.ml_service import MLService
from tests.conftest import make_device, make_org, make_user


def _spec(name: str):
    return next(s for s in SIGNALS if s.signal == name)


# ── Forecasting ──────────────────────────────────────────────────────────────

def test_forecast_falling_battery_projects_time_to_threshold() -> None:
    rng = np.random.default_rng(0)
    hours = np.linspace(-6, 0, 600)
    values = 12.7 - 0.1 * hours + rng.normal(0, 0.03, hours.size)   # falling 0.1 V/h, now 12.7 V
    fs = forecast_signal(_spec("battery_voltage"), hours, values)
    assert fs is not None
    assert fs.slope_per_hour == pytest.approx(-0.1, abs=0.01)
    assert fs.hours_to_threshold == pytest.approx(12.0, abs=1.0)      # (11.5 − 12.7) / −0.1
    assert fs.risk == "CRITICAL"                                       # < 24 h


def test_forecast_flat_signal_has_no_eta() -> None:
    rng = np.random.default_rng(1)
    hours = np.linspace(-6, 0, 600)
    fs = forecast_signal(_spec("engine_temp"), hours, 88 + rng.normal(0, 2, hours.size))
    assert fs.hours_to_threshold is None
    assert fs.risk == "NONE"


def test_forecast_already_past_threshold_is_critical() -> None:
    hours = np.linspace(-6, 0, 300)
    fs = forecast_signal(_spec("engine_temp"), hours, 118 + hours * -0.5 + 5)   # ends ≈ 123 °C
    assert fs.hours_to_threshold == 0.0
    assert fs.risk == "CRITICAL"


def test_forecast_ignores_missing_optional_sensor() -> None:
    hours = np.linspace(-6, 0, 300)
    assert forecast_signal(_spec("oil_pressure"), hours, np.full(hours.size, np.nan)) is None


# ── Fault classification ─────────────────────────────────────────────────────

class _R:
    def __init__(self, **kw):
        base = dict(engine_temp=88.0, rpm=1500.0, battery_voltage=13.5, speed=50.0, vibration=1.0,
                    oil_pressure=None, coolant_level=None, tire_pressure=None, dtc_codes=None)
        base.update(kw)
        self.__dict__.update(base)


@pytest.mark.parametrize("code,fault", [
    ("P0217", FaultType.COOLANT_LEAK), ("P0562", FaultType.BATTERY_FAILURE),
    ("P0730", FaultType.TRANSMISSION_STRESS), ("P0521", FaultType.LOW_OIL_PRESSURE),
    ("C0750", FaultType.TIRE_PRESSURE), ("C0040", FaultType.WHEEL_BEARING),
    ("C0265", FaultType.BRAKE_WEAR), ("P0302", FaultType.ENGINE_STRESS), ("B1234", None),
])
def test_dtc_mapping(code, fault) -> None:
    assert classify_dtc(code) == fault


def test_dtc_outranks_sensor_rules() -> None:
    assert fault_classifier(_R(engine_temp=125.0, dtc_codes=["P0562"])) == (FaultType.BATTERY_FAILURE, FaultConfidence.HIGH)


@pytest.mark.parametrize("kw,fault", [
    ({"vibration": 8.5, "speed": 95}, FaultType.WHEEL_BEARING),
    ({"oil_pressure": 8.0}, FaultType.LOW_OIL_PRESSURE),
    ({"tire_pressure": 60.0}, FaultType.TIRE_PRESSURE),
    ({"coolant_level": 25.0, "engine_temp": 104.0}, FaultType.COOLANT_LEAK),
    ({"engine_temp": 115.0}, FaultType.COOLANT_LEAK),
    ({"battery_voltage": 11.4}, FaultType.BATTERY_FAILURE),
    ({"rpm": 4200, "speed": 90}, FaultType.TRANSMISSION_STRESS),
    ({"rpm": 4200, "engine_temp": 115}, FaultType.ENGINE_STRESS),
    ({}, FaultType.UNKNOWN_ANOMALY),
])
def test_sensor_rules(kw, fault) -> None:
    assert fault_classifier(_R(**kw))[0] == fault


# ── Signed artifacts & drift ─────────────────────────────────────────────────

def _normal_matrix(n: int = 200, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    records = [_R(engine_temp=85 + rng.normal(0, 3), rpm=1500 + rng.normal(0, 200), battery_voltage=13.5,
                  speed=60 + rng.normal(0, 10), vibration=1.2 + rng.normal(0, 0.2)) for _ in range(n)]
    for r in records:
        r.fuel_level = 60.0
    return _extract_features(records)


def test_artifact_roundtrip_and_tamper_rejection() -> None:
    signed = _serialize(_train(_normal_matrix()))
    assert _deserialize(signed) is not None
    tampered = signed[:40] + bytes([signed[40] ^ 0xFF]) + signed[41:]
    assert _deserialize(tampered) is None                 # never unpickled
    assert _deserialize(b"\x00" * 10) is None


def test_psi_detects_shift() -> None:
    bundle = _train(_normal_matrix(seed=0))
    same = population_stability_index(bundle, _normal_matrix(seed=1))
    shifted = _normal_matrix(seed=2)
    shifted[:, 0] += 25                                    # engine running 25 °C hotter
    moved = population_stability_index(bundle, shifted)
    assert same < 0.1
    assert moved > 0.25


# ── Registry integration ─────────────────────────────────────────────────────

async def _seed(db: AsyncSession, device, n: int, start: datetime, seed: int = 42) -> None:
    rng = np.random.default_rng(seed)
    for i in range(n):
        db.add(Telemetry(
            id=uuid.uuid4(), device_id=device.id, recorded_at=start + timedelta(seconds=2 * i),
            gps_lat=51.5, gps_lon=-0.1, engine_temp=85 + rng.normal(0, 3), rpm=1500 + rng.normal(0, 200),
            fuel_level=60.0, battery_voltage=13.5 + rng.normal(0, 0.1), speed=60 + rng.normal(0, 10),
            vibration=1.2 + abs(rng.normal(0, 0.2)),
        ))
    await db.flush()


@pytest.mark.asyncio
async def test_first_run_registers_model_and_advances_watermark(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    device = await make_device(db_session, org)
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await _seed(db_session, device, 60, start)
    await AnomalyService(db_session).run_for_device(device.id, since=start + timedelta(seconds=80))

    versions = (await db_session.execute(select(ModelVersion))).scalars().all()
    assert len(versions) == 1
    v = versions[0]
    assert (v.scope, v.version, v.reason, v.is_active) == ("DEVICE", 1, "INITIAL", True)
    assert v.n_train == 40                                 # only readings *before* the scoring window
    assert "engine_temp" in v.feature_stats
    assert device.anomaly_watermark is not None

    # Nothing new → nothing scored, no new model
    assert await AnomalyService(db_session).run_for_device(device.id) == []
    assert len((await db_session.execute(select(ModelVersion))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_class_model_scores_a_brand_new_vehicle(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    veteran = await make_device(db_session, org, name="Veteran")
    rookie = await make_device(db_session, org, name="Rookie")
    await _seed(db_session, veteran, 400, datetime.now(timezone.utc) - timedelta(hours=2))
    await _seed(db_session, rookie, 3, datetime.now(timezone.utc) - timedelta(minutes=1), seed=7)

    await AnomalyService(db_session).run_for_device(rookie.id)
    scopes = {(v.scope, v.device_type) for v in (await db_session.execute(select(ModelVersion))).scalars().all()}
    assert ("CLASS", "truck") in scopes


@pytest.mark.asyncio
async def test_pin_and_detector_metrics(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    user = await make_user(db_session, org, "ml@test.com")
    device = await make_device(db_session, org)
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await _seed(db_session, device, 60, start)
    await AnomalyService(db_session).run_for_device(device.id, since=start + timedelta(seconds=80))
    [v] = await MLService(db_session).list_versions(user, device.id)
    assert v.has_artifact

    pinned = await MLService(db_session).pin(v.id, user, True)
    assert pinned.pinned and pinned.is_active

    metrics = await MLService(db_session).detector_metrics(user)
    assert metrics.labelled_alerts == 0
    assert metrics.precision is None


@pytest.mark.asyncio
async def test_forecast_service_on_degrading_vehicle(db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    device = await make_device(db_session, org, name="Gamma")
    now = datetime.now(timezone.utc)
    rng = np.random.default_rng(3)
    for i in range(300):
        t = now - timedelta(seconds=60 * (300 - i))                       # 5 h of data
        h = -(300 - i) / 60
        db_session.add(Telemetry(
            id=uuid.uuid4(), device_id=device.id, recorded_at=t, gps_lat=51.5, gps_lon=-0.1,
            engine_temp=88 + rng.normal(0, 2), rpm=1500, fuel_level=60, speed=50, vibration=1.0,
            battery_voltage=12.6 - 0.15 * h + rng.normal(0, 0.03),
        ))
    await db_session.flush()
    fc = await ForecastService(db_session).forecast_device(device)
    battery = next(s for s in fc.signals if s.signal == "battery_voltage")
    assert battery.hours_to_threshold == pytest.approx(7.3, abs=1.0)       # (11.5 − 12.6) / −0.15
    assert fc.overall_risk == "CRITICAL"
    assert fc.min_hours_to_failure == battery.hours_to_threshold
