"""
Management reporting: period KPIs, maintenance cost and downtime, and an
estimate of the cost avoided by catching faults early.

Cost-avoided model
──────────────────
For every work order resolved with a *confirmed* fault as its root cause, the
saving is the typical cost of that fault if run to failure (tow, major repair,
lost day) minus what the early repair actually cost. The failure costs below
are illustrative heavy-vehicle estimates; tune FAILURE_COSTS for your fleet.
"""

import csv
import io
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import as_utc
from app.models.alert import Alert, AlertFeedback
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.fleet import FuelEvent, Trip
from app.models.maintenance import WorkOrder
from app.models.organization import Organization
from app.models.telemetry import Telemetry
from app.models.user import User
from app.services.access import get_org_device, org_device_ids

# fault → (typical early-repair cost, typical run-to-failure cost)
FAILURE_COSTS: dict[str, tuple[float, float]] = {
    "COOLANT_LEAK":        (200.0, 12000.0),
    "BATTERY_FAILURE":     (250.0, 1500.0),
    "TRANSMISSION_STRESS": (800.0, 6000.0),
    "BRAKE_WEAR":          (300.0, 2500.0),
    "ENGINE_STRESS":       (500.0, 10000.0),
    "WHEEL_BEARING":       (350.0, 4000.0),
    "LOW_OIL_PRESSURE":    (150.0, 12000.0),
    "TIRE_PRESSURE":       (50.0, 900.0),
}

EXPORT_LIMIT = 50_000


def cost_avoided_for(root_cause: str | None, actual_cost: float) -> float:
    if not root_cause or root_cause not in FAILURE_COSTS:
        return 0.0
    early, failure = FAILURE_COSTS[root_cause]
    spent = actual_cost if actual_cost > 0 else early
    return max(0.0, failure - spent)


def _minutes(a: datetime | None, b: datetime | None) -> float | None:
    if not a or not b:
        return None
    return (as_utc(b) - as_utc(a)).total_seconds() / 60.0


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


class ReportService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def summary(self, org_id: uuid.UUID, start: datetime, end: datetime) -> dict[str, Any]:
        org = await self._db.get(Organization, org_id)
        devices = (
            await self._db.execute(select(Device).where(Device.organization_id == org_id))
        ).scalars().unique().all()
        names = {d.id: d.name for d in devices}
        visible = select(Device.id).where(Device.organization_id == org_id)

        trips = (
            await self._db.execute(
                select(Trip).where(Trip.device_id.in_(visible), Trip.started_at >= start, Trip.started_at <= end)
            )
        ).scalars().unique().all()
        alerts = (
            await self._db.execute(
                select(Alert).where(Alert.device_id.in_(visible), Alert.created_at >= start, Alert.created_at <= end)
            )
        ).scalars().all()
        work_orders = (
            await self._db.execute(
                select(WorkOrder).where(
                    WorkOrder.organization_id == org_id,
                    WorkOrder.created_at >= start, WorkOrder.created_at <= end,
                )
            )
        ).scalars().unique().all()
        resolved = (
            await self._db.execute(
                select(WorkOrder).where(
                    WorkOrder.organization_id == org_id,
                    WorkOrder.status == "RESOLVED",
                    WorkOrder.resolved_at >= start, WorkOrder.resolved_at <= end,
                )
            )
        ).scalars().unique().all()
        thefts = (
            await self._db.execute(
                select(FuelEvent).where(
                    FuelEvent.device_id.in_(visible), FuelEvent.event == "THEFT_SUSPECTED",
                    FuelEvent.occurred_at >= start, FuelEvent.occurred_at <= end,
                )
            )
        ).scalars().unique().all()

        price = org.fuel_price_per_liter if org else 0.0
        distance = sum(t.distance_km for t in trips)
        fuel = sum(t.fuel_used_liters for t in trips)

        by_sev: dict[str, int] = defaultdict(int)
        by_fault: dict[str, int] = defaultdict(int)
        ack_minutes: list[float] = []
        tp = fp = 0
        for a in alerts:
            by_sev[a.severity.value] += 1
            by_fault[a.fault_type.value if a.fault_type else "UNCLASSIFIED"] += 1
            m = _minutes(a.created_at, a.acknowledged_at)
            if m is not None and m >= 0:
                ack_minutes.append(m)
            if a.feedback == AlertFeedback.TRUE_POSITIVE.value:
                tp += 1
            elif a.feedback == AlertFeedback.FALSE_POSITIVE.value:
                fp += 1

        resolve_hours = [
            m / 60.0 for w in resolved if (m := _minutes(w.created_at, w.resolved_at)) is not None
        ]
        maint_cost = sum((w.parts_cost or 0) + (w.labor_cost or 0) for w in resolved)
        downtime = sum(w.downtime_hours or 0 for w in resolved)

        avoided_rows: dict[str, dict[str, float]] = {}
        for w in resolved:
            saved = cost_avoided_for(w.root_cause, (w.parts_cost or 0) + (w.labor_cost or 0))
            if saved > 0:
                row = avoided_rows.setdefault(w.root_cause, {"count": 0, "saved": 0.0})
                row["count"] += 1
                row["saved"] += saved

        per_vehicle: dict[uuid.UUID, dict[str, Any]] = {
            d.id: {"device_id": str(d.id), "device_name": d.name, "distance_km": 0.0, "fuel_liters": 0.0,
                   "trips": 0, "alerts": 0, "critical_alerts": 0, "work_orders": 0,
                   "maintenance_cost": 0.0, "downtime_hours": 0.0, "_scores": []}
            for d in devices
        }
        for t in trips:
            v = per_vehicle.get(t.device_id)
            if v:
                v["distance_km"] += t.distance_km
                v["fuel_liters"] += t.fuel_used_liters
                v["trips"] += 1
                v["_scores"].append(t.score)
        for a in alerts:
            v = per_vehicle.get(a.device_id)
            if v:
                v["alerts"] += 1
                v["critical_alerts"] += 1 if a.severity.value == "CRITICAL" else 0
        for w in work_orders:
            v = per_vehicle.get(w.device_id)
            if v:
                v["work_orders"] += 1
        for w in resolved:
            v = per_vehicle.get(w.device_id)
            if v:
                v["maintenance_cost"] += (w.parts_cost or 0) + (w.labor_cost or 0)
                v["downtime_hours"] += w.downtime_hours or 0
        vehicles = []
        for v in per_vehicle.values():
            scores = v.pop("_scores")
            v["avg_trip_score"] = _mean(scores)
            v["distance_km"] = round(v["distance_km"], 1)
            v["fuel_liters"] = round(v["fuel_liters"], 1)
            v["maintenance_cost"] = round(v["maintenance_cost"], 2)
            vehicles.append(v)
        vehicles.sort(key=lambda v: (-v["critical_alerts"], -v["alerts"], v["device_name"]))

        return {
            "period": {"start": as_utc(start).isoformat(), "end": as_utc(end).isoformat()},
            "organization": org.name if org else None,
            "currency": org.currency if org else "USD",
            "fleet": {
                "vehicles": len(devices),
                "trips": len(trips),
                "distance_km": round(distance, 1),
                "driving_hours": round(sum(t.duration_seconds for t in trips) / 3600.0, 1),
                "fuel_liters": round(fuel, 1),
                "fuel_cost": round(fuel * price, 2),
                "l_per_100km": round(100.0 * fuel / distance, 1) if distance > 1 else None,
                "avg_trip_score": _mean([t.score for t in trips]),
                "fuel_theft_suspected": len(thefts),
            },
            "alerts": {
                "total": len(alerts),
                "by_severity": dict(by_sev),
                "by_fault_type": dict(by_fault),
                "acknowledged_pct": round(100.0 * sum(1 for a in alerts if a.acknowledged) / len(alerts), 1) if alerts else None,
                "mean_minutes_to_acknowledge": _mean(ack_minutes),
                "true_positive": tp,
                "false_positive": fp,
                "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
            },
            "maintenance": {
                "work_orders_created": len(work_orders),
                "work_orders_resolved": len(resolved),
                "total_cost": round(maint_cost, 2),
                "downtime_hours": round(downtime, 1),
                "mean_hours_to_resolve": _mean(resolve_hours),
            },
            "cost_avoided": {
                "total": round(sum(r["saved"] for r in avoided_rows.values()), 2),
                "by_fault_type": [
                    {"fault_type": k, "count": int(v["count"]), "saved": round(v["saved"], 2)}
                    for k, v in sorted(avoided_rows.items(), key=lambda kv: -kv[1]["saved"])
                ],
                "assumptions": {k: {"early_repair": e, "run_to_failure": f} for k, (e, f) in FAILURE_COSTS.items()},
            },
            "vehicles": vehicles,
            "device_names": {str(k): v for k, v in names.items()},
        }

    # ── CSV exports ──────────────────────────────────────────────────────────

    async def export_csv(
        self, kind: str, requester: User, start: datetime, end: datetime, device_id: uuid.UUID | None = None
    ) -> str:
        visible = org_device_ids(requester)
        names = {
            d.id: d.name
            for d in (
                await self._db.execute(select(Device).where(Device.organization_id == requester.organization_id))
            ).scalars().unique().all()
        }
        buf = io.StringIO()
        w = csv.writer(buf)

        def dev_filter(col):
            return [col == device_id] if device_id else []

        if kind == "alerts":
            w.writerow(["created_at", "vehicle", "severity", "fault_type", "fault_confidence", "anomaly_score",
                        "acknowledged", "acknowledged_at", "feedback", "summary"])
            rows = (await self._db.execute(
                select(Alert).where(Alert.device_id.in_(visible), Alert.created_at.between(start, end),
                                    *dev_filter(Alert.device_id))
                .order_by(Alert.created_at).limit(EXPORT_LIMIT))).scalars().all()
            for a in rows:
                w.writerow([as_utc(a.created_at).isoformat(), names.get(a.device_id), a.severity.value,
                            a.fault_type.value if a.fault_type else "", a.fault_confidence.value if a.fault_confidence else "",
                            round(a.anomaly_score, 4), a.acknowledged,
                            as_utc(a.acknowledged_at).isoformat() if a.acknowledged_at else "",
                            a.feedback or "", (a.llm_summary or "").replace("\n", " ")])
        elif kind == "trips":
            w.writerow(["started_at", "ended_at", "vehicle", "driver", "distance_km", "duration_min", "avg_speed",
                        "max_speed", "idle_min", "harsh_accel", "harsh_brake", "overspeed_s", "fuel_l", "score"])
            rows = (await self._db.execute(
                select(Trip).where(Trip.device_id.in_(visible), Trip.started_at.between(start, end),
                                   *dev_filter(Trip.device_id))
                .order_by(Trip.started_at).limit(EXPORT_LIMIT))).scalars().unique().all()
            for t in rows:
                w.writerow([as_utc(t.started_at).isoformat(), as_utc(t.ended_at).isoformat(), names.get(t.device_id),
                            t.driver.name if t.driver else "", round(t.distance_km, 2),
                            round(t.duration_seconds / 60, 1), t.avg_speed, round(t.max_speed, 1),
                            round(t.idle_seconds / 60, 1), t.harsh_accel_count, t.harsh_brake_count,
                            round(t.overspeed_seconds), round(t.fuel_used_liters, 2), t.score])
        elif kind == "work_orders":
            w.writerow(["number", "created_at", "vehicle", "title", "status", "priority", "root_cause",
                        "parts_cost", "labor_cost", "downtime_hours", "resolved_at"])
            rows = (await self._db.execute(
                select(WorkOrder).where(WorkOrder.organization_id == requester.organization_id,
                                        WorkOrder.created_at.between(start, end), *dev_filter(WorkOrder.device_id))
                .order_by(WorkOrder.number).limit(EXPORT_LIMIT))).scalars().unique().all()
            for o in rows:
                w.writerow([o.number, as_utc(o.created_at).isoformat(), names.get(o.device_id), o.title, o.status,
                            o.priority, o.root_cause or "", o.parts_cost, o.labor_cost, o.downtime_hours,
                            as_utc(o.resolved_at).isoformat() if o.resolved_at else ""])
        elif kind == "fuel_events":
            w.writerow(["occurred_at", "vehicle", "event", "level_before", "level_after", "liters", "lat", "lon", "reviewed"])
            rows = (await self._db.execute(
                select(FuelEvent).where(FuelEvent.device_id.in_(visible), FuelEvent.occurred_at.between(start, end),
                                        *dev_filter(FuelEvent.device_id))
                .order_by(FuelEvent.occurred_at).limit(EXPORT_LIMIT))).scalars().unique().all()
            for e in rows:
                w.writerow([as_utc(e.occurred_at).isoformat(), names.get(e.device_id), e.event, e.level_before,
                            e.level_after, e.liters, e.lat, e.lon, e.reviewed])
        elif kind == "telemetry":
            if not device_id:
                raise ValueError("device_id is required for telemetry exports")
            await get_org_device(self._db, device_id, requester)
            cols = ["recorded_at", "gps_lat", "gps_lon", "engine_temp", "rpm", "fuel_level", "battery_voltage",
                    "speed", "vibration", "oil_pressure", "coolant_level", "tire_pressure", "ambient_temp"]
            w.writerow(cols + ["dtc_codes"])
            rows = (await self._db.execute(
                select(Telemetry).where(Telemetry.device_id == device_id, Telemetry.recorded_at.between(start, end))
                .order_by(Telemetry.recorded_at).limit(EXPORT_LIMIT))).scalars().all()
            for r in rows:
                w.writerow([as_utc(r.recorded_at).isoformat()] + [getattr(r, c) for c in cols[1:]]
                           + [" ".join(r.dtc_codes or [])])
        elif kind == "audit":
            w.writerow(["created_at", "user", "action", "entity_type", "entity_id", "details"])
            rows = (await self._db.execute(
                select(AuditLog).where(AuditLog.organization_id == requester.organization_id,
                                       AuditLog.created_at.between(start, end))
                .order_by(AuditLog.created_at).limit(EXPORT_LIMIT))).scalars().all()
            for a in rows:
                w.writerow([as_utc(a.created_at).isoformat(), a.user_email, a.action, a.entity_type,
                            a.entity_id or "", a.details])
        else:
            raise ValueError(f"Unknown export type '{kind}'")
        return buf.getvalue()
