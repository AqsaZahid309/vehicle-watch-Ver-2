"""
Analytics service: fleet-wide health summaries and per-device trends.

All aggregations are pushed down to PostgreSQL via SQLAlchemy func — we never
pull full telemetry tables into Python memory.
"""

import uuid
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ForbiddenError
from app.models.alert import Alert
from app.models.device import Device
from app.models.telemetry import Telemetry
from app.models.user import User, UserRole


class AnalyticsService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def fleet_summary(self, requester: User) -> dict[str, Any]:
        """Aggregate health metrics across all devices the requester can see."""
        device_stmt = select(Device)
        if requester.role != UserRole.ADMIN:
            device_stmt = device_stmt.where(Device.owner_id == requester.id)
        devices = list((await self._db.execute(device_stmt)).scalars().all())
        device_ids = [d.id for d in devices]

        if not device_ids:
            return {
                "total_devices": 0,
                "active_devices": 0,
                "total_alerts": 0,
                "unacknowledged_alerts": 0,
                "alerts_by_severity": {},
                "avg_engine_temp": None,
                "avg_fuel_level": None,
            }

        active_count = sum(1 for d in devices if d.is_active)

        # Alert counts grouped by severity
        alert_result = await self._db.execute(
            select(Alert.severity, func.count(Alert.id).label("cnt"))
            .where(Alert.device_id.in_(device_ids))
            .group_by(Alert.severity)
        )
        alerts_by_severity: dict[str, int] = {}
        total_alerts = 0
        for row in alert_result:
            alerts_by_severity[row.severity.value] = row.cnt
            total_alerts += row.cnt

        unack_result = await self._db.execute(
            select(func.count(Alert.id))
            .where(Alert.device_id.in_(device_ids))
            .where(Alert.acknowledged.is_(False))
        )
        unacknowledged = unack_result.scalar_one()

        avg_result = await self._db.execute(
            select(
                func.avg(Telemetry.engine_temp).label("avg_engine_temp"),
                func.avg(Telemetry.fuel_level).label("avg_fuel_level"),
            ).where(Telemetry.device_id.in_(device_ids))
        )
        row = avg_result.one()

        return {
            "total_devices": len(devices),
            "active_devices": active_count,
            "total_alerts": total_alerts,
            "unacknowledged_alerts": unacknowledged,
            "alerts_by_severity": alerts_by_severity,
            "avg_engine_temp": round(row.avg_engine_temp, 2) if row.avg_engine_temp else None,
            "avg_fuel_level": round(row.avg_fuel_level, 2) if row.avg_fuel_level else None,
        }

    async def device_trends(
        self, device_id: uuid.UUID, requester: User, last_n: int = 100
    ) -> dict[str, Any]:
        result = await self._db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if not device:
            raise NotFoundError("Device", str(device_id))
        if requester.role != UserRole.ADMIN and device.owner_id != requester.id:
            raise ForbiddenError("You do not have access to this device")

        # Alert distribution — computed regardless of telemetry presence
        alert_result = await self._db.execute(
            select(Alert.severity, func.count(Alert.id).label("cnt"))
            .where(Alert.device_id == device_id)
            .group_by(Alert.severity)
        )
        alert_counts: dict[str, int] = {row.severity.value: row.cnt for row in alert_result}

        tel_result = await self._db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.recorded_at.desc())
            .limit(last_n)
        )
        records = list(tel_result.scalars().all())

        # Base response shape is always consistent — no missing keys on empty data
        base: dict[str, Any] = {
            "device_id": str(device_id),
            "device_name": device.name,
            "device_type": device.device_type,
            "sample_count": len(records),
            "trends": {},
            "alert_counts": alert_counts,
        }

        if not records:
            return base

        fields = ["engine_temp", "rpm", "fuel_level", "battery_voltage", "speed", "vibration"]
        for field in fields:
            values = [getattr(r, field) for r in records]
            base["trends"][field] = {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "latest": round(values[0], 2),
            }

        return base
