"""
Remaining-useful-life forecasting.

The anomaly detector reacts once a reading already looks wrong. This service
looks at the *trend* of each health signal and projects when it will cross its
failure threshold — "Truck-Gamma's battery reaches 11.5 V in about 3 hours".

Method (deliberately simple and explainable):
  1. Take the last FORECAST_WINDOW_HOURS of readings (up to MAX_POINTS).
  2. Split them into equal time buckets and take the median of each bucket —
     medians shrug off the single-reading spikes that plague raw sensor data.
  3. Fit a straight line (least squares) through the bucket medians.
  4. If the slope points toward the threshold and the fit is good (R² ≥ 0.5),
     hours_to_threshold = (threshold − current) / slope.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import as_utc, utcnow
from app.models.device import Device
from app.models.telemetry import Telemetry
from app.schemas.ml import DeviceForecast, ForecastSignal

FORECAST_WINDOW_HOURS = 6.0
MAX_POINTS = 3000
MIN_POINTS = 30
BUCKETS = 24
MIN_R2 = 0.5
HORIZON_HOURS = 24 * 30


@dataclass(frozen=True)
class SignalSpec:
    signal: str
    label: str
    unit: str
    threshold: float
    direction: str  # rising | falling — which way is "toward failure"
    predicted_fault: str


SIGNALS: list[SignalSpec] = [
    SignalSpec("engine_temp", "Engine temperature", "°C", 120.0, "rising", "COOLANT_LEAK"),
    SignalSpec("battery_voltage", "Battery voltage", "V", 11.5, "falling", "BATTERY_FAILURE"),
    SignalSpec("vibration", "Vibration", "g", 8.0, "rising", "WHEEL_BEARING"),
    SignalSpec("oil_pressure", "Oil pressure", "psi", 20.0, "falling", "LOW_OIL_PRESSURE"),
    SignalSpec("coolant_level", "Coolant level", "%", 30.0, "falling", "COOLANT_LEAK"),
    SignalSpec("tire_pressure", "Lowest tyre pressure", "psi", 85.0, "falling", "TIRE_PRESSURE"),
]

_RISK_ORDER = ["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


def risk_for_hours(hours: float | None, already_past: bool) -> str:
    if already_past:
        return "CRITICAL"
    if hours is None:
        return "NONE"
    if hours <= 24:
        return "CRITICAL"
    if hours <= 72:
        return "HIGH"
    if hours <= 168:
        return "MEDIUM"
    return "LOW"


def forecast_signal(spec: SignalSpec, hours: np.ndarray, values: np.ndarray) -> ForecastSignal | None:
    """hours: time of each reading relative to the latest reading (≤ 0)."""
    mask = ~np.isnan(values)
    hours, values = hours[mask], values[mask]
    if len(values) < MIN_POINTS:
        return None

    span = hours.max() - hours.min()
    if span <= 0:
        return None
    n_buckets = min(BUCKETS, max(4, len(values) // 5))
    edges = np.linspace(hours.min(), hours.max() + 1e-9, n_buckets + 1)
    bx, by = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (hours >= lo) & (hours < hi)
        if sel.sum() >= 2:
            bx.append(float(np.median(hours[sel])))
            by.append(float(np.median(values[sel])))
    if len(bx) < 4:
        return None

    x, y = np.array(bx), np.array(by)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    current = float(intercept)  # the fitted value "now" (x = 0)

    toward = slope > 0 if spec.direction == "rising" else slope < 0
    past = current >= spec.threshold if spec.direction == "rising" else current <= spec.threshold
    hours_to: float | None = None
    if not past and toward and r2 >= MIN_R2 and abs(slope) > 1e-9:
        h = (spec.threshold - current) / slope
        if 0 < h <= HORIZON_HOURS:
            hours_to = round(h, 1)

    return ForecastSignal(
        signal=spec.signal,
        label=spec.label,
        unit=spec.unit,
        current=round(current, 2),
        threshold=spec.threshold,
        direction=spec.direction,
        slope_per_hour=round(float(slope), 4),
        r2=round(max(0.0, r2), 3),
        hours_to_threshold=0.0 if past else hours_to,
        risk=risk_for_hours(hours_to, past),
        predicted_fault=spec.predicted_fault,
        history=[{"h": round(a, 2), "v": round(b, 3)} for a, b in zip(bx, by)],
    )


class ForecastService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def forecast_device(self, device: Device) -> DeviceForecast:
        since = utcnow() - timedelta(hours=FORECAST_WINDOW_HOURS)
        rows = list(
            (
                await self._db.execute(
                    select(Telemetry)
                    .where(Telemetry.device_id == device.id, Telemetry.recorded_at >= since)
                    .order_by(Telemetry.recorded_at.desc())
                    .limit(MAX_POINTS)
                )
            ).scalars().all()
        )
        base = DeviceForecast(
            device_id=device.id, device_name=device.name, sample_count=len(rows),
            window_hours=0.0, overall_risk="NONE", min_hours_to_failure=None, signals=[],
        )
        if len(rows) < MIN_POINTS:
            return base

        latest = as_utc(rows[0].recorded_at)
        hours = np.array([(as_utc(r.recorded_at) - latest).total_seconds() / 3600.0 for r in rows])
        base.window_hours = round(float(-hours.min()), 2)

        signals: list[ForecastSignal] = []
        for spec in SIGNALS:
            values = np.array(
                [getattr(r, spec.signal) if getattr(r, spec.signal) is not None else np.nan for r in rows],
                dtype=float,
            )
            fs = forecast_signal(spec, hours, values)
            if fs:
                signals.append(fs)

        signals.sort(key=lambda s: (-_RISK_ORDER.index(s.risk), s.hours_to_threshold or 1e9))
        base.signals = signals
        if signals:
            base.overall_risk = signals[0].risk
            finite = [s.hours_to_threshold for s in signals if s.hours_to_threshold is not None]
            base.min_hours_to_failure = min(finite) if finite else None
        return base

    async def forecast_fleet(self, org_id: uuid.UUID) -> list[DeviceForecast]:
        devices = (
            await self._db.execute(
                select(Device).where(Device.organization_id == org_id, Device.is_active.is_(True))
            )
        ).scalars().all()
        out = [await self.forecast_device(d) for d in devices]
        out.sort(key=lambda f: (-_RISK_ORDER.index(f.overall_risk), f.min_hours_to_failure or 1e9))
        return out
