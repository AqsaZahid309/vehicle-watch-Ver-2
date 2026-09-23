"""
Background worker.

One cycle every ANOMALY_WORKER_INTERVAL_SECONDS:

  per device (each in its own short transaction)
    1. anomaly detection      → alerts
    2. trips / geofences / fuel / hourly rollups
  then, outside any transaction
    3. LLM summaries for new alerts (slow network call — never holds a DB lock)
    4. notifications for alerts and fleet events, delivered to channels
  periodic jobs (each guarded by a Redis key so they run once fleet-wide)
    5. escalation of unacknowledged CRITICAL alerts        (every cycle)
    6. failure forecasts → FORECAST notifications          (every 10 min)
    7. preventive-maintenance schedules → work orders      (every 10 min)
    8. telemetry retention                                  (daily)
    9. weekly report notification                           (Mondays)

Safe to run in several places at once: a Redis lock lets only one instance
execute a cycle, and every watermark lives in the database, so restarts and
failovers resume exactly where the previous cycle stopped.
"""

import asyncio
import logging
import secrets
import time
import uuid
from datetime import timedelta

from sqlalchemy import delete, select

from app.config import get_settings
from app.core import events
from app.core.metrics import WORKER_CYCLE_SECONDS, WORKER_LAST_SUCCESS
from app.core.utils import as_utc, utcnow
from app.database import AsyncSessionLocal
from app.models.alert import Alert, AlertSeverity
from app.models.device import Device
from app.models.fleet import Geofence
from app.models.maintenance import ServiceSchedule, WorkOrder
from app.models.notification import EventType, Notification
from app.models.organization import Organization
from app.models.telemetry import Telemetry
from app.redis import get_redis_binary_pool, get_redis_pool
from app.services.anomaly_service import AnomalyService
from app.services.fleet_processor import FleetProcessor
from app.services.forecast_service import ForecastService
from app.services.gemini_service import GeminiService
from app.services.maintenance_service import next_work_order_number, schedule_status
from app.services.notification_service import NotificationService, deliver_in_new_session
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)
settings = get_settings()

_LOCK_KEY = "vw:worker:lock"
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_gemini_service: GeminiService | None = None


def _get_gemini_service() -> GeminiService:
    global _gemini_service
    if _gemini_service is None:
        _gemini_service = GeminiService()
    return _gemini_service


def _redis_text():
    try:
        return get_redis_pool()
    except RuntimeError:
        return None


def _redis_binary():
    try:
        return get_redis_binary_pool()
    except RuntimeError:
        return None


async def _once_per(redis, key: str, seconds: int) -> bool:
    """True if this instance won the right to run `key` in the current period."""
    if redis is None:
        return True
    try:
        return bool(await redis.set(f"vw:worker:task:{key}", "1", nx=True, ex=seconds))
    except Exception:
        return False


# ── Per-device processing ─────────────────────────────────────────────────────

async def _process_device(device_id: uuid.UUID, geofences_by_org: dict, redis_text, redis_bin) -> list[uuid.UUID]:
    """Run detection + fleet processing for one device. Returns notification IDs to deliver."""
    notify_ids: list[uuid.UUID] = []
    new_alert_ids: list[uuid.UUID] = []

    async with AsyncSessionLocal() as db:
        device = await db.get(Device, device_id)
        if device is None:
            return []
        org_id = device.organization_id

        alerts = await AnomalyService(db, redis=redis_bin).run_for_device(device.id)
        new_alert_ids = [a.id for a in alerts]

        if org_id not in geofences_by_org:
            geofences_by_org[org_id] = list(
                (await db.execute(select(Geofence).where(Geofence.organization_id == org_id))).scalars().all()
            )
        fleet = await FleetProcessor(db).process_device(device, geofences_by_org[org_id])

        notifier = NotificationService(db, redis_text)
        for ev in fleet.events:
            n = await notifier.create(org_id, ev.event_type, ev.title, ev.body,
                                      severity=ev.severity, device_id=ev.device_id, link=ev.link)
            notify_ids.append(n.id)
        await db.commit()

        device_name, device_type = device.name, device.device_type

    if new_alert_ids:
        logger.info("Worker: %d new alert(s) for %s", len(new_alert_ids), device_name)
        notify_ids += await _summarise_and_notify(new_alert_ids, org_id, device_id, device_name, device_type, redis_text)
    return notify_ids


async def _summarise_and_notify(
    alert_ids: list[uuid.UUID], org_id: uuid.UUID, device_id: uuid.UUID,
    device_name: str, device_type: str, redis_text,
) -> list[uuid.UUID]:
    async with AsyncSessionLocal() as db:
        alerts = list((await db.execute(select(Alert).where(Alert.id.in_(alert_ids)))).scalars().all())
        inputs = [(a.id, a.anomaly_score, a.affected_metrics,
                   a.fault_type.value if a.fault_type else None,
                   a.fault_confidence.value if a.fault_confidence else None) for a in alerts]

    # LLM calls happen with no session open.
    gemini = _get_gemini_service()
    summaries: dict[uuid.UUID, str] = {}
    for alert_id, score, metrics, fault, conf in inputs:
        summary = await gemini.generate_alert_summary(
            device_type=device_type, device_name=device_name, anomaly_score=score,
            affected_metrics=metrics, fault_type=fault, fault_confidence=conf,
        )
        if summary:
            summaries[alert_id] = summary

    notify_ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as db:
        notifier = NotificationService(db, redis_text)
        alerts = list((await db.execute(select(Alert).where(Alert.id.in_(alert_ids)))).scalars().all())
        for a in alerts:
            if a.id in summaries:
                a.llm_summary = summaries[a.id]
            fault = (a.fault_type.value if a.fault_type else "ANOMALY").replace("_", " ").title()
            await events.publish(redis_text, org_id, "alert", {
                "id": str(a.id), "device_id": str(device_id), "device_name": device_name,
                "severity": a.severity.value, "fault_type": a.fault_type.value if a.fault_type else None,
                "anomaly_score": a.anomaly_score, "created_at": as_utc(a.created_at).isoformat(),
            })
            # LOW alerts stay in the alert list; only MEDIUM+ interrupt people.
            if a.severity != AlertSeverity.LOW:
                n = await notifier.create(
                    org_id, EventType.ALERT.value, f"{a.severity.value}: {fault} — {device_name}",
                    a.llm_summary or f"Anomaly score {a.anomaly_score:.3f}",
                    severity=a.severity.value, device_id=device_id, alert_id=a.id, link=f"/alerts?id={a.id}",
                )
                notify_ids.append(n.id)
        await db.commit()
    return notify_ids


# ── Periodic jobs ─────────────────────────────────────────────────────────────

async def _escalate(redis_text) -> list[uuid.UUID]:
    """Re-notify CRITICAL alerts nobody has acknowledged within the org's escalation window."""
    ids: list[uuid.UUID] = []
    now = utcnow()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Alert, Device, Organization)
                .join(Device, Device.id == Alert.device_id)
                .join(Organization, Organization.id == Device.organization_id)
                .where(
                    Alert.severity == AlertSeverity.CRITICAL,
                    Alert.acknowledged.is_(False),
                    Alert.escalated_at.is_(None),
                    Alert.created_at >= now - timedelta(days=2),
                )
            )
        ).unique().all()
        notifier = NotificationService(db, redis_text)
        for alert, device, org in rows:
            if now - as_utc(alert.created_at) < timedelta(minutes=org.escalation_minutes):
                continue
            alert.escalated_at = now
            fault = (alert.fault_type.value if alert.fault_type else "ANOMALY").replace("_", " ").title()
            n = await notifier.create(
                org.id, EventType.ESCALATION.value,
                f"ESCALATION: unacknowledged {fault} on {device.name}",
                f"A CRITICAL alert on {device.name} has been open for over {org.escalation_minutes} minutes "
                f"without acknowledgement. {alert.llm_summary or ''}".strip(),
                severity="CRITICAL", device_id=device.id, alert_id=alert.id, link=f"/alerts?id={alert.id}",
            )
            ids.append(n.id)
        await db.commit()
    return ids


async def _forecasts(redis_text) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as db:
        devices = (await db.execute(select(Device).where(Device.is_active.is_(True)))).scalars().unique().all()
        svc = ForecastService(db)
        notifier = NotificationService(db, redis_text)
        for device in devices:
            fc = await svc.forecast_device(device)
            for s in fc.signals:
                if s.risk not in ("HIGH", "CRITICAL"):
                    continue
                if not await _once_per(redis_text, f"forecast:{device.id}:{s.signal}", 6 * 3600):
                    continue
                when = "now" if s.hours_to_threshold == 0 else f"in ~{s.hours_to_threshold:.1f} h"
                n = await notifier.create(
                    device.organization_id, EventType.FORECAST.value,
                    f"Predicted {s.predicted_fault.replace('_', ' ').lower()} — {device.name}",
                    f"{s.label} on {device.name} is trending {s.direction} ({s.slope_per_hour:+.3f} {s.unit}/h, "
                    f"now {s.current} {s.unit}) and will cross {s.threshold} {s.unit} {when}. "
                    f"Plan maintenance before then.",
                    severity="CRITICAL" if s.risk == "CRITICAL" else "MEDIUM",
                    device_id=device.id, link=f"/vehicles/{device.id}",
                )
                ids.append(n.id)
        await db.commit()
    return ids


async def _service_schedules(redis_text) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ServiceSchedule, Device)
                .join(Device, Device.id == ServiceSchedule.device_id)
                .where(ServiceSchedule.is_active.is_(True))
            )
        ).unique().all()
        notifier = NotificationService(db, redis_text)
        for schedule, device in rows:
            _, _, due = schedule_status(schedule, device)
            if not due:
                continue
            open_wo = (
                await db.execute(
                    select(WorkOrder.id).where(
                        WorkOrder.schedule_id == schedule.id,
                        WorkOrder.status.in_(("OPEN", "IN_PROGRESS", "ON_HOLD")),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if open_wo:
                continue
            wo = WorkOrder(
                organization_id=device.organization_id,
                number=await next_work_order_number(db, device.organization_id),
                device_id=device.id, schedule_id=schedule.id,
                title=f"Scheduled: {schedule.name} — {device.name}",
                description=f"Preventive maintenance '{schedule.name}' is due.",
                priority="MEDIUM",
            )
            db.add(wo)
            await db.flush()
            n = await notifier.create(
                device.organization_id, EventType.MAINTENANCE_DUE.value,
                f"Service due: {schedule.name} — {device.name}",
                f"Work order #{wo.number} was opened automatically for '{schedule.name}'.",
                severity="LOW", device_id=device.id, link="/maintenance",
            )
            ids.append(n.id)
        await db.commit()
    return ids


async def _retention() -> None:
    cutoff = utcnow() - timedelta(days=settings.telemetry_retention_days)
    async with AsyncSessionLocal() as db:
        t = await db.execute(delete(Telemetry).where(Telemetry.recorded_at < cutoff))
        n = await db.execute(delete(Notification).where(Notification.created_at < utcnow() - timedelta(days=90)))
        await db.commit()
    logger.info("Retention: removed %s telemetry rows, %s notifications", t.rowcount, n.rowcount)


async def _weekly_reports(redis_text) -> list[uuid.UUID]:
    now = utcnow()
    if now.weekday() != 0 or now.hour < 7:
        return []
    ids: list[uuid.UUID] = []
    year, week, _ = now.isocalendar()
    async with AsyncSessionLocal() as db:
        orgs = (await db.execute(select(Organization))).scalars().all()
        notifier = NotificationService(db, redis_text)
        for org in orgs:
            if not await _once_per(redis_text, f"report:{org.id}:{year}-{week}", 8 * 86400):
                continue
            s = await ReportService(db).summary(org.id, now - timedelta(days=7), now)
            cur = s["currency"]
            body = (
                f"Last 7 days: {s['fleet']['distance_km']} km over {s['fleet']['trips']} trips, "
                f"{s['alerts']['total']} alerts ({s['alerts']['by_severity'].get('CRITICAL', 0)} critical), "
                f"{s['maintenance']['work_orders_resolved']} work orders resolved "
                f"({cur} {s['maintenance']['total_cost']:,.0f}), fuel {s['fleet']['fuel_liters']} L "
                f"({cur} {s['fleet']['fuel_cost']:,.0f}). Estimated cost avoided: {cur} {s['cost_avoided']['total']:,.0f}."
            )
            n = await notifier.create(org.id, EventType.REPORT.value, "Weekly fleet report", body, link="/reports")
            ids.append(n.id)
        await db.commit()
    return ids


# ── Cycle ─────────────────────────────────────────────────────────────────────

async def _run_anomaly_detection_cycle() -> None:
    redis_text, redis_bin = _redis_text(), _redis_binary()
    token = secrets.token_hex(8)
    if redis_text is not None:
        try:
            got = await redis_text.set(_LOCK_KEY, token, nx=True, ex=max(120, settings.anomaly_worker_interval_seconds * 3))
            if not got:
                logger.info("Worker: another instance holds the cycle lock — skipping")
                return
        except Exception as exc:
            logger.warning("Worker: could not take Redis lock (%s) — running unlocked", exc)

    start = time.perf_counter()
    notify_ids: list[uuid.UUID] = []
    failed = 0
    try:
        async with AsyncSessionLocal() as db:
            device_ids = list(
                (await db.execute(select(Device.id).where(Device.is_active.is_(True)))).scalars().all()
            )
        logger.info("Worker: cycle starting for %d active devices", len(device_ids))

        geofences_by_org: dict = {}
        for device_id in device_ids:
            try:
                notify_ids += await _process_device(device_id, geofences_by_org, redis_text, redis_bin)
            except Exception as exc:
                failed += 1
                logger.exception("Worker: device %s failed: %s", device_id, exc)

        for name, job, period in (
            ("escalation", _escalate, 0),
            ("forecast", _forecasts, 600),
            ("schedules", _service_schedules, 600),
            ("weekly_report", _weekly_reports, 3600),
        ):
            if period and not await _once_per(redis_text, name, period):
                continue
            try:
                notify_ids += await job(redis_text)
            except Exception as exc:
                logger.exception("Worker: job %s failed: %s", name, exc)

        if await _once_per(redis_text, "retention", 86400):
            try:
                await _retention()
            except Exception as exc:
                logger.exception("Worker: retention failed: %s", exc)

        await deliver_in_new_session(notify_ids)
        WORKER_LAST_SUCCESS.set(time.time())
    finally:
        duration = time.perf_counter() - start
        WORKER_CYCLE_SECONDS.observe(duration)
        logger.info("Worker: cycle done in %.2fs (%d device failures, %d notifications)",
                    duration, failed, len(notify_ids))
        if redis_text is not None:
            try:
                await redis_text.eval(_RELEASE_SCRIPT, 1, _LOCK_KEY, token)
            except Exception:
                pass


async def start_anomaly_worker() -> None:
    logger.info("Worker: starting with %ds interval", settings.anomaly_worker_interval_seconds)
    while True:
        try:
            await _run_anomaly_detection_cycle()
        except Exception as exc:  # never let the loop die
            logger.exception("Worker: cycle crashed: %s", exc)
        await asyncio.sleep(settings.anomaly_worker_interval_seconds)
