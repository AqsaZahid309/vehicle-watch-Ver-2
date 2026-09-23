"""Trips, driver behaviour, geofences, fuel events and hourly rollups."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fleet import Driver, FuelEvent, Geofence, GeofenceEvent, Trip
from app.models.telemetry import Telemetry, TelemetryHourly
from app.services.fleet_processor import FleetProcessor, geofence_contains, score_trip
from tests.conftest import make_device, make_org

DEPOT = (51.5000, -0.1000)


def _reading(device_id, t: datetime, lat: float, lon: float, speed: float, fuel: float = 60.0, rpm: float = 1500.0):
    return Telemetry(
        id=uuid.uuid4(), device_id=device_id, recorded_at=t, gps_lat=lat, gps_lon=lon,
        engine_temp=85.0, rpm=rpm, fuel_level=fuel, battery_voltage=13.5, speed=speed, vibration=1.0,
    )


async def _setup(db: AsyncSession):
    org = await make_org(db)
    driver = Driver(id=uuid.uuid4(), organization_id=org.id, name="Sam Driver")
    db.add(driver)
    await db.flush()
    device = await make_device(db, org)
    device.assigned_driver_id = driver.id
    device.fuel_tank_liters = 400.0
    await db.flush()
    return org, device, driver


@pytest.mark.asyncio
async def test_trip_building_distance_and_harsh_events(db_session: AsyncSession) -> None:
    org, device, driver = await _setup(db_session)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    # Drive east at ~72 km/h: 0.0004° lon every 2 s ≈ 27.7 m at this latitude.
    speeds = [60, 62, 64, 66, 68, 70, 72, 72, 72, 30, 30, 32]   # 72→30 in 2 s = harsh brake
    for i, v in enumerate(speeds):
        db_session.add(_reading(device.id, start + timedelta(seconds=2 * i), DEPOT[0], DEPOT[1] + 0.0004 * i, v,
                                fuel=60.0 - 0.01 * i))
    # A second trip after a 20-minute gap
    later = start + timedelta(minutes=20)
    for i in range(5):
        db_session.add(_reading(device.id, later + timedelta(seconds=2 * i), DEPOT[0], DEPOT[1], 0))
    await db_session.flush()

    result = await FleetProcessor(db_session).process_device(device, [])
    await db_session.flush()

    trips = (await db_session.execute(select(Trip).order_by(Trip.started_at))).scalars().unique().all()
    assert len(trips) == 2
    first = trips[0]
    assert first.is_open is False                       # closed by the gap
    assert first.driver_id == driver.id
    assert first.harsh_brake_count == 1
    assert first.point_count == len(speeds)
    assert 0.25 < first.distance_km < 0.35              # 11 steps × ~27.7 m
    assert first.fuel_used_liters == pytest.approx(0.11 / 100 * 400, rel=0.05)
    assert first.score < 100
    assert result.trips_closed >= 1
    assert device.odometer_km == pytest.approx(first.distance_km, rel=0.01)
    assert device.trip_watermark is not None


@pytest.mark.asyncio
async def test_processing_is_incremental(db_session: AsyncSession) -> None:
    org, device, _ = await _setup(db_session)
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    for i in range(5):
        db_session.add(_reading(device.id, start + timedelta(seconds=2 * i), DEPOT[0], DEPOT[1], 40))
    await db_session.flush()
    proc = FleetProcessor(db_session)
    await proc.process_device(device, [])
    await db_session.flush()
    for i in range(5, 10):
        db_session.add(_reading(device.id, start + timedelta(seconds=2 * i), DEPOT[0], DEPOT[1], 40))
    await db_session.flush()
    await proc.process_device(device, [])
    await db_session.flush()
    trips = (await db_session.execute(select(Trip))).scalars().unique().all()
    assert len(trips) == 1
    assert trips[0].point_count == 10
    assert trips[0].is_open is True


@pytest.mark.asyncio
async def test_geofence_enter_exit_and_speeding(db_session: AsyncSession) -> None:
    org, device, _ = await _setup(db_session)
    fence = Geofence(id=uuid.uuid4(), organization_id=org.id, name="Depot", kind="RESTRICTED", shape="CIRCLE",
                     center_lat=DEPOT[0], center_lon=DEPOT[1], radius_m=200, alert_on_exit=True,
                     speed_limit_kmh=20)
    db_session.add(fence)
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    path = [(0.01, 10), (0.0, 10), (0.0, 35), (0.0, 36), (0.0, 10), (0.01, 10)]  # outside → inside → speed → out
    for i, (dlon, v) in enumerate(path):
        db_session.add(_reading(device.id, start + timedelta(seconds=2 * i), DEPOT[0], DEPOT[1] + dlon, v))
    await db_session.flush()

    result = await FleetProcessor(db_session).process_device(device, [fence])
    await db_session.flush()

    events = [e.event for e in (await db_session.execute(
        select(GeofenceEvent).order_by(GeofenceEvent.occurred_at))).scalars().unique().all()]
    assert events == ["ENTER", "SPEEDING", "EXIT"]      # one SPEEDING per episode, not per reading
    kinds = [(e.event_type, e.severity) for e in result.events]
    assert ("GEOFENCE", "CRITICAL") in kinds             # entering a RESTRICTED zone
    assert len(result.events) == 3                       # enter, speeding, exit


@pytest.mark.asyncio
async def test_fuel_theft_and_refuel(db_session: AsyncSession) -> None:
    org, device, _ = await _setup(db_session)
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    levels = [(60, 0), (60, 0), (45, 0), (45, 0), (80, 0), (80, 30)]   # parked drop of 15 %, then refuel
    for i, (fuel, v) in enumerate(levels):
        db_session.add(_reading(device.id, start + timedelta(seconds=2 * i), DEPOT[0], DEPOT[1], v, fuel=fuel))
    await db_session.flush()
    result = await FleetProcessor(db_session).process_device(device, [])
    await db_session.flush()

    events = (await db_session.execute(select(FuelEvent).order_by(FuelEvent.occurred_at))).scalars().unique().all()
    assert [e.event for e in events] == ["THEFT_SUSPECTED", "REFUEL"]
    assert events[0].liters == pytest.approx(60.0)      # 15 % of a 400 L tank
    assert any(e.event_type == "FUEL" and e.severity == "CRITICAL" for e in result.events)
    trip = (await db_session.execute(select(Trip))).scalars().unique().one()
    assert trip.fuel_used_liters == 0                   # theft and refuel are not "consumption"


@pytest.mark.asyncio
async def test_hourly_rollups_merge(db_session: AsyncSession) -> None:
    org, device, _ = await _setup(db_session)
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    for i in range(4):
        db_session.add(_reading(device.id, hour + timedelta(minutes=i), DEPOT[0], DEPOT[1], 10.0 * i))
    await db_session.flush()
    proc = FleetProcessor(db_session)
    await proc.process_device(device, [])
    await db_session.flush()
    for i in range(4, 6):
        db_session.add(_reading(device.id, hour + timedelta(minutes=i), DEPOT[0], DEPOT[1], 10.0 * i))
    await db_session.flush()
    await proc.process_device(device, [])
    await db_session.flush()

    [row] = (await db_session.execute(select(TelemetryHourly))).scalars().all()
    assert row.sample_count == 6
    assert row.avg_speed == pytest.approx(25.0)          # mean of 0..50
    assert row.max_speed == 50.0


def test_geofence_polygon() -> None:
    g = Geofence(shape="POLYGON", polygon=[[0, 0], [0, 1], [1, 1], [1, 0]])
    assert geofence_contains(g, 0.5, 0.5)
    assert not geofence_contains(g, 1.5, 0.5)


def test_trip_score_penalises_bad_driving() -> None:
    now = datetime.now(timezone.utc)
    calm = Trip(started_at=now - timedelta(hours=1), ended_at=now, moving_seconds=3500, idle_seconds=100,
                harsh_accel_count=0, harsh_brake_count=0, overspeed_seconds=0, over_rev_count=0, point_count=1800)
    wild = Trip(started_at=now - timedelta(hours=1), ended_at=now, moving_seconds=2000, idle_seconds=1600,
                harsh_accel_count=6, harsh_brake_count=6, overspeed_seconds=900, over_rev_count=500, point_count=1800)
    assert score_trip(calm) == 100.0
    assert score_trip(wild) < 40
