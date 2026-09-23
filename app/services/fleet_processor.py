"""
Incremental stream processor for trips, driver behaviour, geofences, fuel and
hourly rollups. Runs in the background worker once per cycle per device and
walks the telemetry that arrived since the device's trip_watermark.

All derived state (open trip, inside/outside per geofence) is persisted, so a
restart or a second worker instance continues exactly where the last one
stopped.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.utils import as_utc, haversine_m, point_in_polygon, utcnow
from app.models.device import Device
from app.models.fleet import (
    DeviceGeofenceState, FuelEvent, FuelEventType, Geofence, GeofenceEvent, GeofenceKind, Trip,
)
from app.models.telemetry import Telemetry, TelemetryHourly

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_POINTS_PER_CYCLE = 5000
MOVING_SPEED_KMH = 3.0
# Readings further apart than this are not used for acceleration (the delta is meaningless).
MAX_ACCEL_DT_S = 10.0
# Distance increments implying more than this speed are GPS glitches, not travel.
MAX_PLAUSIBLE_KMH = 250.0
FIRST_RUN_LOOKBACK = timedelta(hours=24)


@dataclass
class FleetEvent:
    """Something a human should hear about — turned into a notification by the worker."""
    event_type: str       # GEOFENCE | FUEL
    severity: str         # LOW | MEDIUM | CRITICAL
    title: str
    body: str
    device_id: uuid.UUID
    link: str | None = None


@dataclass
class ProcessResult:
    points: int = 0
    trips_closed: int = 0
    events: list[FleetEvent] = field(default_factory=list)


def score_trip(trip: Trip) -> float:
    """
    0–100 driver-behaviour score for one trip. Penalties are rate-based (per hour,
    per share of time) so a long trip is not punished just for being long.
    """
    duration_h = max(trip.duration_seconds / 3600.0, 0.25)
    harsh_per_hour = (trip.harsh_accel_count + trip.harsh_brake_count) / duration_h
    overspeed_pct = 100.0 * trip.overspeed_seconds / max(trip.moving_seconds, 1.0)
    over_rev_pct = 100.0 * trip.over_rev_count / max(trip.point_count, 1)
    idle_pct = 100.0 * trip.idle_seconds / max(trip.duration_seconds, 1.0)

    score = 100.0
    score -= min(35.0, 4.0 * harsh_per_hour)
    score -= min(25.0, 0.8 * overspeed_pct)
    score -= min(20.0, 0.5 * over_rev_pct)
    score -= min(20.0, 0.5 * max(0.0, idle_pct - 10.0))
    return round(max(0.0, min(100.0, score)), 1)


def geofence_contains(g: Geofence, lat: float, lon: float) -> bool:
    if g.shape == "POLYGON" and g.polygon:
        return point_in_polygon(lat, lon, g.polygon)
    if g.center_lat is None or g.center_lon is None or not g.radius_m:
        return False
    return haversine_m(lat, lon, g.center_lat, g.center_lon) <= g.radius_m


def _hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


class _HourAgg:
    __slots__ = ("n", "sums", "maxs", "min_batt")

    def __init__(self) -> None:
        self.n = 0
        self.sums = {"engine_temp": 0.0, "rpm": 0.0, "speed": 0.0, "fuel_level": 0.0,
                     "battery_voltage": 0.0, "vibration": 0.0}
        self.maxs = {"engine_temp": float("-inf"), "rpm": float("-inf"),
                     "speed": float("-inf"), "vibration": float("-inf")}
        self.min_batt = float("inf")

    def add(self, r: Telemetry) -> None:
        self.n += 1
        for k in self.sums:
            self.sums[k] += getattr(r, k)
        for k in self.maxs:
            self.maxs[k] = max(self.maxs[k], getattr(r, k))
        self.min_batt = min(self.min_batt, r.battery_voltage)


class FleetProcessor:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _open_trip(self, device_id: uuid.UUID) -> Trip | None:
        return (
            await self._db.execute(
                select(Trip).where(Trip.device_id == device_id, Trip.is_open.is_(True))
                .order_by(Trip.started_at.desc()).limit(1)
            )
        ).scalars().first()

    def _close(self, trip: Trip, result: ProcessResult) -> None:
        trip.is_open = False
        trip.score = score_trip(trip)
        result.trips_closed += 1

    async def process_device(self, device: Device, geofences: list[Geofence]) -> ProcessResult:
        result = ProcessResult()
        now = utcnow()
        gap = timedelta(minutes=settings.trip_gap_minutes)
        watermark = as_utc(device.trip_watermark) or now - FIRST_RUN_LOOKBACK

        records = list(
            (
                await self._db.execute(
                    select(Telemetry)
                    .where(Telemetry.device_id == device.id, Telemetry.recorded_at > watermark)
                    .order_by(Telemetry.recorded_at.asc())
                    .limit(MAX_POINTS_PER_CYCLE)
                )
            ).scalars().all()
        )

        trip = await self._open_trip(device.id)
        states: dict[uuid.UUID, DeviceGeofenceState] = {}
        if geofences and records:
            rows = await self._db.execute(
                select(DeviceGeofenceState).where(DeviceGeofenceState.device_id == device.id)
            )
            states = {s.geofence_id: s for s in rows.scalars().all()}

        hours: dict[datetime, _HourAgg] = {}
        odometer_add = 0.0
        tank = device.fuel_tank_liters or 300.0

        for r in records:
            t = as_utc(r.recorded_at)
            hours.setdefault(_hour(t), _HourAgg()).add(r)
            result.points += 1

            if trip is not None and t - as_utc(trip.ended_at) > gap:
                self._close(trip, result)
                trip = None

            if trip is None:
                trip = Trip(
                    device_id=device.id,
                    driver_id=device.assigned_driver_id,
                    started_at=t, ended_at=t, is_open=True,
                    start_lat=r.gps_lat, start_lon=r.gps_lon, end_lat=r.gps_lat, end_lon=r.gps_lon,
                    distance_km=0.0, moving_seconds=0.0, idle_seconds=0.0,
                    max_speed=r.speed, speed_sum=r.speed, point_count=1,
                    harsh_accel_count=0, harsh_brake_count=0, overspeed_seconds=0.0,
                    over_rev_count=1 if r.rpm > settings.over_rev_rpm else 0,
                    fuel_used_liters=0.0, score=100.0,
                    last_speed=r.speed, last_fuel_level=r.fuel_level,
                )
                self._db.add(trip)
            else:
                dt = (t - as_utc(trip.ended_at)).total_seconds()
                if dt <= 0:
                    continue

                step_km = haversine_m(trip.end_lat, trip.end_lon, r.gps_lat, r.gps_lon) / 1000.0
                if step_km / (dt / 3600.0) <= MAX_PLAUSIBLE_KMH:
                    trip.distance_km += step_km
                    odometer_add += step_km

                if r.speed > MOVING_SPEED_KMH:
                    trip.moving_seconds += dt
                else:
                    trip.idle_seconds += dt

                if dt <= MAX_ACCEL_DT_S:
                    accel = (r.speed - trip.last_speed) / 3.6 / dt
                    if accel > settings.harsh_accel_ms2:
                        trip.harsh_accel_count += 1
                    elif accel < settings.harsh_brake_ms2:
                        trip.harsh_brake_count += 1

                if r.speed > settings.overspeed_kmh:
                    trip.overspeed_seconds += dt
                if r.rpm > settings.over_rev_rpm:
                    trip.over_rev_count += 1

                # Fuel: a jump up is a refuel, a sharp drop while parked is suspicious,
                # anything else is consumption.
                delta_pct = r.fuel_level - trip.last_fuel_level
                stationary = max(trip.last_speed, r.speed) < 5.0
                if delta_pct >= settings.refuel_rise_pct:
                    self._fuel_event(device, r, t, trip.last_fuel_level, FuelEventType.REFUEL, tank)
                elif delta_pct <= -settings.fuel_theft_drop_pct and stationary:
                    liters = self._fuel_event(device, r, t, trip.last_fuel_level,
                                              FuelEventType.THEFT_SUSPECTED, tank)
                    result.events.append(FleetEvent(
                        "FUEL", "CRITICAL",
                        f"Possible fuel theft — {device.name}",
                        f"Fuel dropped {abs(delta_pct):.1f}% (~{liters:.0f} L) while {device.name} "
                        f"was stationary at {r.gps_lat:.5f}, {r.gps_lon:.5f}.",
                        device.id, "/fuel",
                    ))
                elif delta_pct < 0:
                    trip.fuel_used_liters += -delta_pct / 100.0 * tank

                trip.ended_at = t
                trip.end_lat, trip.end_lon = r.gps_lat, r.gps_lon
                trip.max_speed = max(trip.max_speed, r.speed)
                trip.speed_sum += r.speed
                trip.point_count += 1
                trip.last_speed = r.speed
                trip.last_fuel_level = r.fuel_level

            for g in geofences:
                self._check_geofence(device, g, states, r, t, result)

        # A vehicle that stopped reporting has finished its trip.
        if trip is not None and now - as_utc(trip.ended_at) > gap:
            self._close(trip, result)

        if records:
            device.trip_watermark = as_utc(records[-1].recorded_at)
            device.odometer_km = (device.odometer_km or 0.0) + odometer_add
            await self._merge_hourly(device.id, hours)
        return result

    def _fuel_event(
        self, device: Device, r: Telemetry, t: datetime, before: float, kind: FuelEventType, tank: float
    ) -> float:
        liters = abs(r.fuel_level - before) / 100.0 * tank
        self._db.add(FuelEvent(
            device_id=device.id, event=kind.value, occurred_at=t,
            level_before=before, level_after=r.fuel_level, liters=round(liters, 1),
            lat=r.gps_lat, lon=r.gps_lon,
        ))
        return liters

    def _check_geofence(
        self,
        device: Device,
        g: Geofence,
        states: dict[uuid.UUID, DeviceGeofenceState],
        r: Telemetry,
        t: datetime,
        result: ProcessResult,
    ) -> None:
        inside = geofence_contains(g, r.gps_lat, r.gps_lon)
        state = states.get(g.id)
        if state is None:
            # First observation establishes the baseline without firing an event.
            state = DeviceGeofenceState(device_id=device.id, geofence_id=g.id, inside=inside,
                                        speeding=False, since=t)
            self._db.add(state)
            states[g.id] = state
            return

        if state.inside != inside:
            state.inside = inside
            state.since = t
            event = "ENTER" if inside else "EXIT"
            self._db.add(GeofenceEvent(geofence_id=g.id, device_id=device.id, event=event,
                                       occurred_at=t, lat=r.gps_lat, lon=r.gps_lon, speed=r.speed))
            restricted = g.kind == GeofenceKind.RESTRICTED.value
            if (inside and (g.alert_on_enter or restricted)) or (not inside and g.alert_on_exit):
                result.events.append(FleetEvent(
                    "GEOFENCE", "CRITICAL" if restricted and inside else "LOW",
                    f"{device.name} {'entered' if inside else 'left'} {g.name}",
                    f"{device.name} {'entered' if inside else 'left'} the {g.kind.lower()} zone "
                    f"'{g.name}' at {t:%H:%M} UTC (speed {r.speed:.0f} km/h).",
                    device.id, "/geofences",
                ))
            if not inside:
                state.speeding = False

        if inside and g.speed_limit_kmh:
            over = r.speed > g.speed_limit_kmh
            if over and not state.speeding:
                self._db.add(GeofenceEvent(geofence_id=g.id, device_id=device.id, event="SPEEDING",
                                           occurred_at=t, lat=r.gps_lat, lon=r.gps_lon, speed=r.speed))
                result.events.append(FleetEvent(
                    "GEOFENCE", "MEDIUM",
                    f"{device.name} speeding in {g.name}",
                    f"{device.name} reached {r.speed:.0f} km/h in '{g.name}' "
                    f"(limit {g.speed_limit_kmh:.0f} km/h).",
                    device.id, "/geofences",
                ))
            state.speeding = over

    async def _merge_hourly(self, device_id: uuid.UUID, hours: dict[datetime, _HourAgg]) -> None:
        if not hours:
            return
        existing = {
            as_utc(h.bucket): h
            for h in (
                await self._db.execute(
                    select(TelemetryHourly).where(
                        TelemetryHourly.device_id == device_id,
                        TelemetryHourly.bucket.in_(list(hours.keys())),
                    )
                )
            ).scalars().all()
        }
        for bucket, agg in hours.items():
            row = existing.get(bucket)
            if row is None:
                self._db.add(TelemetryHourly(
                    device_id=device_id, bucket=bucket, sample_count=agg.n,
                    avg_engine_temp=agg.sums["engine_temp"] / agg.n, max_engine_temp=agg.maxs["engine_temp"],
                    avg_rpm=agg.sums["rpm"] / agg.n, max_rpm=agg.maxs["rpm"],
                    avg_speed=agg.sums["speed"] / agg.n, max_speed=agg.maxs["speed"],
                    avg_fuel_level=agg.sums["fuel_level"] / agg.n,
                    avg_battery_voltage=agg.sums["battery_voltage"] / agg.n, min_battery_voltage=agg.min_batt,
                    avg_vibration=agg.sums["vibration"] / agg.n, max_vibration=agg.maxs["vibration"],
                ))
                continue
            n_old, n = row.sample_count, row.sample_count + agg.n

            def merge(old_avg: float, key: str) -> float:
                return (old_avg * n_old + agg.sums[key]) / n

            row.avg_engine_temp = merge(row.avg_engine_temp, "engine_temp")
            row.avg_rpm = merge(row.avg_rpm, "rpm")
            row.avg_speed = merge(row.avg_speed, "speed")
            row.avg_fuel_level = merge(row.avg_fuel_level, "fuel_level")
            row.avg_battery_voltage = merge(row.avg_battery_voltage, "battery_voltage")
            row.avg_vibration = merge(row.avg_vibration, "vibration")
            row.max_engine_temp = max(row.max_engine_temp, agg.maxs["engine_temp"])
            row.max_rpm = max(row.max_rpm, agg.maxs["rpm"])
            row.max_speed = max(row.max_speed, agg.maxs["speed"])
            row.max_vibration = max(row.max_vibration, agg.maxs["vibration"])
            row.min_battery_voltage = min(row.min_battery_voltage, agg.min_batt)
            row.sample_count = n
