"""
Background anomaly detection worker.

Runs every ANOMALY_WORKER_INTERVAL_SECONDS (default 60s) via asyncio.
Uses a separate DB session per run to avoid long-lived transactions that
would hold locks and inflate connection pool usage.

Design decision: We use asyncio.create_task + a loop pattern (not APScheduler
or Celery) to keep the deployment footprint minimal — no additional services
or brokers required for this use case.

GeminiService is instantiated ONCE here (module-level singleton) and reused
across all cycles. Constructing it per-cycle wastes SDK setup overhead.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.device import Device
from app.redis import get_redis_binary_pool
from app.services.anomaly_service import AnomalyService
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)
settings = get_settings()

# Module-level singleton — initialized once, reused every cycle
_gemini_service: GeminiService | None = None
_last_run_at: datetime | None = None


def _get_gemini_service() -> GeminiService:
    global _gemini_service
    if _gemini_service is None:
        _gemini_service = GeminiService()
    return _gemini_service


async def _run_anomaly_detection_cycle() -> None:
    global _last_run_at

    run_start = datetime.now(timezone.utc)
    logger.info("Anomaly worker: starting cycle at %s", run_start.isoformat())
    cycle_succeeded = False

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Device).where(Device.is_active.is_(True)))
            devices = list(result.scalars().all())

            logger.info("Anomaly worker: processing %d active devices", len(devices))

            try:
                redis = get_redis_binary_pool()
            except RuntimeError:
                redis = None

            anomaly_svc = AnomalyService(db, redis=redis)
            gemini_svc = _get_gemini_service()

            for device in devices:
                try:
                    alerts = await anomaly_svc.run_for_device(device.id, since=_last_run_at)

                    for alert in alerts:
                        summary = await gemini_svc.generate_alert_summary(
                            device_type=device.device_type,
                            device_name=device.name,
                            anomaly_score=alert.anomaly_score,
                            affected_metrics=alert.affected_metrics,
                            fault_type=(
                                alert.fault_type.value if alert.fault_type else None
                            ),
                            fault_confidence=(
                                alert.fault_confidence.value if alert.fault_confidence else None
                            ),
                        )
                        if summary:
                            alert.llm_summary = summary

                    if alerts:
                        logger.info(
                            "Anomaly worker: created %d alert(s) for device %s",
                            len(alerts),
                            device.id,
                        )

                except Exception as exc:
                    logger.exception(
                        "Anomaly worker: error processing device %s: %s", device.id, exc
                    )

            await db.commit()
            cycle_succeeded = True

    except Exception as exc:
        logger.exception("Anomaly worker: cycle failed: %s", exc)

    # Only advance the timestamp on success so that records created during a
    # failed cycle are picked up on the next successful run.
    if cycle_succeeded:
        _last_run_at = run_start

    logger.info(
        "Anomaly worker: cycle %s, duration %.2fs",
        "complete" if cycle_succeeded else "FAILED",
        (datetime.now(timezone.utc) - run_start).total_seconds(),
    )


async def start_anomaly_worker() -> None:
    logger.info(
        "Anomaly worker: starting with %ds interval", settings.anomaly_worker_interval_seconds
    )
    while True:
        await _run_anomaly_detection_cycle()
        await asyncio.sleep(settings.anomaly_worker_interval_seconds)
