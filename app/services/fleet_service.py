import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.utils import utcnow
from app.models.device import Device
from app.models.fleet import (
    DeviceGeofenceState, Driver, FuelEvent, FuelEventType, Geofence, GeofenceEvent, Trip,
)
from app.models.organization import Organization
from app.models.telemetry import Telemetry
from app.models.user import User
from app.schemas.fleet import (
    DriverCreate, DriverScore, DriverUpdate, FuelEventRead, FuelVehicleStat, GeofenceCreate,
    GeofenceEventRead, GeofenceRead, GeofenceUpdate, PaginatedTrips, TripPoint, TripRead,
)
from app.services.access import org_device_ids
from app.services.audit_service import record_audit

MAX_ROUTE_POINTS = 2000


def trip_read(t: Trip) -> TripRead:
    read = TripRead.model_validate(t)
    read.device_name = t.device.name if t.device else None
    read.driver_name = t.driver.name if t.driver else None
    return read


class FleetService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Drivers ──────────────────────────────────────────────────────────────

    async def list_drivers(self, requester: User) -> list[Driver]:
        return list(
            (
                await self._db.execute(
                    select(Driver).where(Driver.organization_id == requester.organization_id).order_by(Driver.name)
                )
            ).scalars().all()
        )

    async def _driver(self, driver_id: uuid.UUID, requester: User) -> Driver:
        d = (
            await self._db.execute(
                select(Driver).where(Driver.id == driver_id, Driver.organization_id == requester.organization_id)
            )
        ).scalar_one_or_none()
        if not d:
            raise NotFoundError("Driver", str(driver_id))
        return d

    async def create_driver(self, data: DriverCreate, requester: User) -> Driver:
        d = Driver(organization_id=requester.organization_id, **data.model_dump())
        self._db.add(d)
        await self._db.flush()
        await self._db.refresh(d)
        record_audit(self._db, requester, "driver.created", "driver", d.id, {"name": d.name})
        return d

    async def update_driver(self, driver_id: uuid.UUID, data: DriverUpdate, requester: User) -> Driver:
        d = await self._driver(driver_id, requester)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(d, field, value)
        await self._db.flush()
        await self._db.refresh(d)
        return d

    async def delete_driver(self, driver_id: uuid.UUID, requester: User) -> None:
        d = await self._driver(driver_id, requester)
        record_audit(self._db, requester, "driver.deleted", "driver", d.id, {"name": d.name})
        await self._db.delete(d)

    async def driver_scores(self, requester: User, days: int = 30) -> list[DriverScore]:
        since = utcnow() - timedelta(days=days)
        trips = (
            await self._db.execute(
                select(Trip).where(
                    Trip.device_id.in_(org_device_ids(requester)),
                    Trip.started_at >= since,
                )
            )
        ).scalars().unique().all()

        groups: dict[uuid.UUID | None, list[Trip]] = defaultdict(list)
        for t in trips:
            groups[t.driver_id].append(t)

        out: list[DriverScore] = []
        for driver_id, items in groups.items():
            dist = sum(t.distance_km for t in items)
            dur = sum(t.duration_seconds for t in items)
            moving = sum(t.moving_seconds for t in items)
            idle = sum(t.idle_seconds for t in items)
            harsh_a = sum(t.harsh_accel_count for t in items)
            harsh_b = sum(t.harsh_brake_count for t in items)
            over_s = sum(t.overspeed_seconds for t in items)
            fuel = sum(t.fuel_used_liters for t in items)
            per100 = 100.0 / dist if dist > 1 else 0.0
            # Duration-weighted mean of trip scores: long trips count more.
            weight = sum(max(t.duration_seconds, 1.0) for t in items)
            score = sum(t.score * max(t.duration_seconds, 1.0) for t in items) / weight
            name = items[0].driver.name if items[0].driver else "Unassigned"
            out.append(DriverScore(
                driver_id=driver_id,
                driver_name=name,
                trips=len(items),
                distance_km=round(dist, 1),
                driving_hours=round(dur / 3600.0, 2),
                idle_pct=round(100.0 * idle / dur, 1) if dur else 0.0,
                harsh_accel_per_100km=round(harsh_a * per100, 2),
                harsh_brake_per_100km=round(harsh_b * per100, 2),
                overspeed_pct=round(100.0 * over_s / moving, 1) if moving else 0.0,
                over_rev_events=sum(t.over_rev_count for t in items),
                fuel_l_per_100km=round(fuel * per100, 1) if dist > 1 else None,
                score=round(score, 1),
            ))
        out.sort(key=lambda s: -s.score)
        return out

    # ── Trips ────────────────────────────────────────────────────────────────

    async def list_trips(
        self,
        requester: User,
        device_id: uuid.UUID | None = None,
        driver_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedTrips:
        conditions = [Trip.device_id.in_(org_device_ids(requester))]
        if device_id:
            conditions.append(Trip.device_id == device_id)
        if driver_id:
            conditions.append(Trip.driver_id == driver_id)
        if start:
            conditions.append(Trip.started_at >= start)
        if end:
            conditions.append(Trip.started_at <= end)
        total = (await self._db.execute(select(func.count(Trip.id)).where(*conditions))).scalar_one()
        rows = (
            await self._db.execute(
                select(Trip).where(*conditions).order_by(Trip.started_at.desc())
                .offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().unique().all()
        return PaginatedTrips(
            items=[trip_read(t) for t in rows], total=total, page=page, page_size=page_size,
            pages=max(1, math.ceil(total / page_size)),
        )

    async def _trip(self, trip_id: uuid.UUID, requester: User) -> Trip:
        t = (
            await self._db.execute(
                select(Trip).where(Trip.id == trip_id, Trip.device_id.in_(org_device_ids(requester)))
            )
        ).scalars().first()
        if not t:
            raise NotFoundError("Trip", str(trip_id))
        return t

    async def get_trip(self, trip_id: uuid.UUID, requester: User) -> TripRead:
        return trip_read(await self._trip(trip_id, requester))

    async def trip_route(self, trip_id: uuid.UUID, requester: User) -> list[TripPoint]:
        t = await self._trip(trip_id, requester)
        rows = (
            await self._db.execute(
                select(Telemetry.recorded_at, Telemetry.gps_lat, Telemetry.gps_lon, Telemetry.speed)
                .where(
                    Telemetry.device_id == t.device_id,
                    Telemetry.recorded_at >= t.started_at,
                    Telemetry.recorded_at <= t.ended_at,
                )
                .order_by(Telemetry.recorded_at)
            )
        ).all()
        step = max(1, len(rows) // MAX_ROUTE_POINTS)
        return [TripPoint(t=r[0], lat=r[1], lon=r[2], speed=r[3]) for r in rows[::step]]

    # ── Geofences ────────────────────────────────────────────────────────────

    async def list_geofences(self, requester: User) -> list[GeofenceRead]:
        fences = (
            await self._db.execute(
                select(Geofence).where(Geofence.organization_id == requester.organization_id).order_by(Geofence.name)
            )
        ).scalars().all()
        inside_counts = dict(
            (
                await self._db.execute(
                    select(DeviceGeofenceState.geofence_id, func.count())
                    .where(
                        DeviceGeofenceState.inside.is_(True),
                        DeviceGeofenceState.geofence_id.in_([g.id for g in fences] or [uuid.uuid4()]),
                    )
                    .group_by(DeviceGeofenceState.geofence_id)
                )
            ).all()
        )
        out = []
        for g in fences:
            read = GeofenceRead.model_validate(g)
            read.vehicles_inside = inside_counts.get(g.id, 0)
            out.append(read)
        return out

    async def _geofence(self, geofence_id: uuid.UUID, requester: User) -> Geofence:
        g = (
            await self._db.execute(
                select(Geofence).where(Geofence.id == geofence_id, Geofence.organization_id == requester.organization_id)
            )
        ).scalar_one_or_none()
        if not g:
            raise NotFoundError("Geofence", str(geofence_id))
        return g

    async def create_geofence(self, data: GeofenceCreate, requester: User) -> Geofence:
        g = Geofence(organization_id=requester.organization_id, **data.model_dump())
        g.kind = data.kind.value
        self._db.add(g)
        await self._db.flush()
        await self._db.refresh(g)
        record_audit(self._db, requester, "geofence.created", "geofence", g.id, {"name": g.name})
        return g

    async def update_geofence(self, geofence_id: uuid.UUID, data: GeofenceUpdate, requester: User) -> Geofence:
        g = await self._geofence(geofence_id, requester)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(g, field, value.value if hasattr(value, "value") else value)
        await self._db.flush()
        await self._db.refresh(g)
        return g

    async def delete_geofence(self, geofence_id: uuid.UUID, requester: User) -> None:
        g = await self._geofence(geofence_id, requester)
        record_audit(self._db, requester, "geofence.deleted", "geofence", g.id, {"name": g.name})
        await self._db.delete(g)

    async def geofence_events(
        self, requester: User, geofence_id: uuid.UUID | None = None, device_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[GeofenceEventRead]:
        stmt = select(GeofenceEvent).where(GeofenceEvent.device_id.in_(org_device_ids(requester)))
        if geofence_id:
            stmt = stmt.where(GeofenceEvent.geofence_id == geofence_id)
        if device_id:
            stmt = stmt.where(GeofenceEvent.device_id == device_id)
        rows = (await self._db.execute(stmt.order_by(GeofenceEvent.occurred_at.desc()).limit(limit))).scalars().unique().all()
        return [
            GeofenceEventRead(
                id=e.id, geofence_id=e.geofence_id, geofence_name=e.geofence.name, geofence_kind=e.geofence.kind,
                device_id=e.device_id, device_name=e.device.name, event=e.event, occurred_at=e.occurred_at,
                lat=e.lat, lon=e.lon, speed=e.speed,
            )
            for e in rows
        ]

    # ── Fuel ─────────────────────────────────────────────────────────────────

    async def fuel_events(
        self, requester: User, device_id: uuid.UUID | None = None, event: str | None = None, limit: int = 100
    ) -> list[FuelEventRead]:
        stmt = select(FuelEvent).where(FuelEvent.device_id.in_(org_device_ids(requester)))
        if device_id:
            stmt = stmt.where(FuelEvent.device_id == device_id)
        if event:
            stmt = stmt.where(FuelEvent.event == event)
        rows = (await self._db.execute(stmt.order_by(FuelEvent.occurred_at.desc()).limit(limit))).scalars().unique().all()
        return [
            FuelEventRead(
                id=e.id, device_id=e.device_id, device_name=e.device.name, event=e.event,
                occurred_at=e.occurred_at, level_before=e.level_before, level_after=e.level_after,
                liters=e.liters, lat=e.lat, lon=e.lon, reviewed=e.reviewed,
            )
            for e in rows
        ]

    async def mark_fuel_event_reviewed(self, event_id: uuid.UUID, requester: User) -> None:
        e = (
            await self._db.execute(
                select(FuelEvent).where(FuelEvent.id == event_id, FuelEvent.device_id.in_(org_device_ids(requester)))
            )
        ).scalars().first()
        if not e:
            raise NotFoundError("Fuel event", str(event_id))
        e.reviewed = True
        record_audit(self._db, requester, "fuel_event.reviewed", "fuel_event", e.id)

    async def fuel_stats(self, requester: User, days: int = 30) -> list[FuelVehicleStat]:
        since = utcnow() - timedelta(days=days)
        org = await self._db.get(Organization, requester.organization_id)
        price = org.fuel_price_per_liter if org else 0.0

        devices = (
            await self._db.execute(select(Device).where(Device.organization_id == requester.organization_id))
        ).scalars().unique().all()
        trip_rows = dict(
            (r[0], (r[1], r[2]))
            for r in (
                await self._db.execute(
                    select(Trip.device_id, func.sum(Trip.distance_km), func.sum(Trip.fuel_used_liters))
                    .where(Trip.device_id.in_(org_device_ids(requester)), Trip.started_at >= since)
                    .group_by(Trip.device_id)
                )
            ).all()
        )
        event_counts: dict[tuple[uuid.UUID, str], int] = {
            (r[0], r[1]): r[2]
            for r in (
                await self._db.execute(
                    select(FuelEvent.device_id, FuelEvent.event, func.count())
                    .where(FuelEvent.device_id.in_(org_device_ids(requester)), FuelEvent.occurred_at >= since)
                    .group_by(FuelEvent.device_id, FuelEvent.event)
                )
            ).all()
        }
        latest_fuel: dict[uuid.UUID, float] = {}
        for d in devices:
            v = (
                await self._db.execute(
                    select(Telemetry.fuel_level).where(Telemetry.device_id == d.id)
                    .order_by(Telemetry.recorded_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if v is not None:
                latest_fuel[d.id] = v

        out = []
        for d in devices:
            dist, fuel = trip_rows.get(d.id, (0.0, 0.0))
            dist, fuel = float(dist or 0.0), float(fuel or 0.0)
            cost = fuel * price
            out.append(FuelVehicleStat(
                device_id=d.id, device_name=d.name,
                distance_km=round(dist, 1), fuel_used_liters=round(fuel, 1),
                l_per_100km=round(100.0 * fuel / dist, 1) if dist > 1 else None,
                fuel_cost=round(cost, 2),
                cost_per_km=round(cost / dist, 3) if dist > 1 else None,
                refuels=event_counts.get((d.id, FuelEventType.REFUEL.value), 0),
                theft_suspected=event_counts.get((d.id, FuelEventType.THEFT_SUSPECTED.value), 0),
                current_level=latest_fuel.get(d.id),
            ))
        out.sort(key=lambda s: -(s.fuel_used_liters))
        return out

