"""
Analytics service: fleet-wide health summaries and per-device trends.

All aggregations are pushed down to the database. Fleet averages read the last
24 hours only — the previous implementation averaged the entire telemetry
table on every dashboard refresh. Long-range charts read the hourly rollups.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import as_utc, utcnow
from app.models.alert import Alert
from app.models.device import Device
from app.models.fleet import Trip
from app.models.maintenance import WorkOrder
from app.models.telemetry import Telemetry, TelemetryHourly
from app.models.user import User
from app.services.access import get_org_device

TREND_FIELDS = ["engine_temp", "rpm", "fuel_level", "battery_voltage", "speed", "vibration"]
_OPEN_WO = ("OPEN", "IN_PROGRESS", "ON_HOLD")


class AnalyticsService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def fleet_summary(self, requester: User) -> dict[str, Any]:
        """Aggregate health metrics across all devices in the requester's organization."""
        devices = list(
            (
                await self._db.execute(
                    select(Device).where(Device.organization_id == requester.organization_id)
                )
            ).scalars().unique().all()
        )
        device_ids = [d.id for d in devices]

        if not device_ids:
            return {
                "total_devices": 0, "active_devices": 0, "online_devices": 0,
                "total_alerts": 0, "unacknowledged_alerts": 0,
                "alerts_by_severity": {}, "alerts_by_fault_type": {}, "alerts_last_24h": 0,
                "avg_engine_temp": None, "avg_fuel_level": None,
                "open_work_orders": 0, "distance_km_24h": 0.0, "trips_24h": 0,
            }

        now = utcnow()
        day_ago = now - timedelta(hours=24)
        active_count = sum(1 for d in devices if d.is_active)
        online = sum(1 for d in devices if d.last_seen_at and now - as_utc(d.last_seen_at) < timedelta(minutes=2))

        alerts_by_severity: dict[str, int] = {}
        total_alerts = 0
        for severity, cnt in await self._db.execute(
            select(Alert.severity, func.count(Alert.id))
            .where(Alert.device_id.in_(device_ids))
            .group_by(Alert.severity)
        ):
            alerts_by_severity[severity.value] = cnt
            total_alerts += cnt

        alerts_by_fault: dict[str, int] = {
            (ft.value if ft else "UNCLASSIFIED"): cnt
            for ft, cnt in await self._db.execute(
                select(Alert.fault_type, func.count(Alert.id))
                .where(Alert.device_id.in_(device_ids))
                .group_by(Alert.fault_type)
            )
        }

        unacknowledged = (
            await self._db.execute(
                select(func.count(Alert.id))
                .where(Alert.device_id.in_(device_ids), Alert.acknowledged.is_(False))
            )
        ).scalar_one()
        alerts_24h = (
            await self._db.execute(
                select(func.count(Alert.id))
                .where(Alert.device_id.in_(device_ids), Alert.created_at >= day_ago)
            )
        ).scalar_one()

        row = (
            await self._db.execute(
                select(
                    func.avg(Telemetry.engine_temp).label("avg_engine_temp"),
                    func.avg(Telemetry.fuel_level).label("avg_fuel_level"),
                ).where(Telemetry.device_id.in_(device_ids), Telemetry.recorded_at >= day_ago)
            )
        ).one()

        open_wo = (
            await self._db.execute(
                select(func.count(WorkOrder.id)).where(
                    WorkOrder.organization_id == requester.organization_id,
                    WorkOrder.status.in_(_OPEN_WO),
                )
            )
        ).scalar_one()
        trip_row = (
            await self._db.execute(
                select(func.count(Trip.id), func.coalesce(func.sum(Trip.distance_km), 0.0))
                .where(Trip.device_id.in_(device_ids), Trip.started_at >= day_ago)
            )
        ).one()

        return {
            "total_devices": len(devices),
            "active_devices": active_count,
            "online_devices": online,
            "total_alerts": total_alerts,
            "unacknowledged_alerts": unacknowledged,
            "alerts_by_severity": alerts_by_severity,
            "alerts_by_fault_type": alerts_by_fault,
            "alerts_last_24h": alerts_24h,
            "avg_engine_temp": round(row.avg_engine_temp, 2) if row.avg_engine_temp else None,
            "avg_fuel_level": round(row.avg_fuel_level, 2) if row.avg_fuel_level else None,
            "open_work_orders": open_wo,
            "distance_km_24h": round(float(trip_row[1] or 0.0), 1),
            "trips_24h": trip_row[0],
        }

    async def alert_timeline(self, requester: User, days: int = 7) -> list[dict[str, Any]]:
        """Daily alert counts by severity — computed in Python to stay dialect-neutral."""
        since = utcnow() - timedelta(days=days)
        rows = (
            await self._db.execute(
                select(Alert.created_at, Alert.severity)
                .join(Device, Device.id == Alert.device_id)
                .where(Device.organization_id == requester.organization_id, Alert.created_at >= since)
            )
        ).all()
        buckets: dict[str, dict[str, int]] = {}
        for i in range(days + 1):
            day = (since + timedelta(days=i)).date().isoformat()
            buckets[day] = {"LOW": 0, "MEDIUM": 0, "CRITICAL": 0}
        for created_at, severity in rows:
            day = as_utc(created_at).date().isoformat()
            if day in buckets:
                buckets[day][severity.value] += 1
        return [{"date": d, **counts} for d, counts in buckets.items()]

    async def device_trends(
        self, device_id: uuid.UUID, requester: User, last_n: int = 100
    ) -> dict[str, Any]:
        device = await get_org_device(self._db, device_id, requester)

        alert_counts: dict[str, int] = {
            severity.value: cnt
            for severity, cnt in await self._db.execute(
                select(Alert.severity, func.count(Alert.id))
                .where(Alert.device_id == device_id)
                .group_by(Alert.severity)
            )
        }

        records = list(
            (
                await self._db.execute(
                    select(Telemetry)
                    .where(Telemetry.device_id == device_id)
                    .order_by(Telemetry.recorded_at.desc())
                    .limit(last_n)
                )
            ).scalars().all()
        )

        base: dict[str, Any] = {
            "device_id": str(device_id),
            "device_name": device.name,
            "device_type": device.device_type,
            "sample_count": len(records),
            "trends": {},
            "alert_counts": alert_counts,
            "series": [],
        }
        if not records:
            return base

        for field in TREND_FIELDS:
            values = [getattr(r, field) for r in records]
            base["trends"][field] = {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "latest": round(values[0], 2),
            }
        base["series"] = [
            {"t": as_utc(r.recorded_at).isoformat(), **{f: getattr(r, f) for f in TREND_FIELDS}}
            for r in reversed(records)
        ]
        return base

    async def device_hourly(self, device_id: uuid.UUID, requester: User, hours: int = 72) -> list[dict[str, Any]]:
        await get_org_device(self._db, device_id, requester)
        since = utcnow() - timedelta(hours=hours)
        rows = (
            await self._db.execute(
                select(TelemetryHourly)
                .where(TelemetryHourly.device_id == device_id, TelemetryHourly.bucket >= since)
                .order_by(TelemetryHourly.bucket)
            )
        ).scalars().all()
        return [
            {
                "bucket": as_utc(h.bucket).isoformat(),
                "samples": h.sample_count,
                "avg_engine_temp": round(h.avg_engine_temp, 2), "max_engine_temp": round(h.max_engine_temp, 2),
                "avg_rpm": round(h.avg_rpm, 1), "max_rpm": round(h.max_rpm, 1),
                "avg_speed": round(h.avg_speed, 1), "max_speed": round(h.max_speed, 1),
                "avg_fuel_level": round(h.avg_fuel_level, 1),
                "avg_battery_voltage": round(h.avg_battery_voltage, 3),
                "min_battery_voltage": round(h.min_battery_voltage, 3),
                "avg_vibration": round(h.avg_vibration, 3), "max_vibration": round(h.max_vibration, 3),
            }
            for h in rows
        ]
