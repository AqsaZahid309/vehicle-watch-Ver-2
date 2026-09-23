"""End-to-end worker cycle against the in-memory database."""

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.alert import Alert, AlertSeverity
from app.models.fleet import Trip
from app.models.maintenance import ServiceSchedule, WorkOrder
from app.models.notification import Notification
from app.models.telemetry import Telemetry
from app.workers import anomaly_worker
from tests.conftest import FakeRedis, make_device, make_org


@pytest.mark.asyncio
async def test_worker_cycle(engine, monkeypatch) -> None:
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    redis = FakeRedis()
    monkeypatch.setattr(anomaly_worker, "AsyncSessionLocal", factory)
    monkeypatch.setattr("app.database.AsyncSessionLocal", factory)
    monkeypatch.setattr(anomaly_worker, "_redis_text", lambda: redis)
    monkeypatch.setattr(anomaly_worker, "_redis_binary", lambda: redis)
    gemini = anomaly_worker._get_gemini_service()
    monkeypatch.setattr(gemini, "_client", None)            # deterministic fallback summaries

    now = datetime.now(timezone.utc)
    async with factory() as db:
        org = await make_org(db)
        org.escalation_minutes = 1
        device = await make_device(db, org)
        device.odometer_km = 20_000
        rng = np.random.default_rng(0)
        for i in range(120):   # 4 minutes of healthy driving, then one badly overheating reading
            db.add(Telemetry(
                id=uuid.uuid4(), device_id=device.id, recorded_at=now - timedelta(seconds=240 - 2 * i),
                gps_lat=51.5, gps_lon=-0.1 + 0.0003 * i, engine_temp=85 + rng.normal(0, 2),
                rpm=1500 + rng.normal(0, 100), fuel_level=60 - 0.01 * i, battery_voltage=13.6,
                speed=55 + rng.normal(0, 3), vibration=1.1,
            ))
        db.add(Telemetry(
            id=uuid.uuid4(), device_id=device.id, recorded_at=now, gps_lat=51.5, gps_lon=-0.064,
            engine_temp=150, rpm=5200, fuel_level=58.8, battery_voltage=10.5, speed=55, vibration=9.5,
        ))
        # An old unacknowledged CRITICAL alert that should escalate
        db.add(Alert(id=uuid.uuid4(), device_id=device.id, severity=AlertSeverity.CRITICAL, anomaly_score=-0.7,
                     affected_metrics={}, created_at=now - timedelta(minutes=30)))
        db.add(ServiceSchedule(id=uuid.uuid4(), device_id=device.id, name="Oil change",
                               interval_km=15_000, last_service_km=0, last_service_at=now))
        await db.commit()
        device_id = device.id

    await anomaly_worker._run_anomaly_detection_cycle()

    async with factory() as db:
        alerts = (await db.execute(select(Alert).where(Alert.device_id == device_id)
                                   .order_by(Alert.created_at))).scalars().all()
        new = [a for a in alerts if a.telemetry_id is not None]
        assert new, "the overheating reading should raise an alert"
        assert all(a.llm_summary for a in new)
        assert alerts[0].escalated_at is not None                      # escalated

        kinds = {n.event_type for n in (await db.execute(select(Notification))).scalars().all()}
        assert {"ESCALATION", "MAINTENANCE_DUE"} <= kinds

        wo = (await db.execute(select(WorkOrder))).scalars().unique().one()
        assert wo.title.startswith("Scheduled: Oil change")

        trip = (await db.execute(select(Trip))).scalars().unique().one()
        assert trip.point_count == 121
        assert trip.distance_km > 1

    # The lock is released and a second cycle is a no-op for already-processed data.
    assert "vw:worker:lock" not in redis.store
    await anomaly_worker._run_anomaly_detection_cycle()
    async with factory() as db:
        assert len((await db.execute(select(WorkOrder))).scalars().unique().all()) == 1
        assert len((await db.execute(select(Alert))).scalars().all()) == len(alerts)


@pytest.mark.asyncio
async def test_worker_skips_when_lock_is_held(monkeypatch) -> None:
    redis = FakeRedis()
    redis.store["vw:worker:lock"] = "someone-else"
    monkeypatch.setattr(anomaly_worker, "_redis_text", lambda: redis)

    called = False

    class Boom:
        def __call__(self, *a, **k):
            nonlocal called
            called = True
            raise AssertionError("should not open a session")

    monkeypatch.setattr(anomaly_worker, "AsyncSessionLocal", Boom())
    await anomaly_worker._run_anomaly_detection_cycle()
    assert called is False
