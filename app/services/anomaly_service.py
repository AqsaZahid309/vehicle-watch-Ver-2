"""
Ensemble Anomaly Detection Pipeline — Isolation Forest + Local Outlier Factor

Architecture decisions
──────────────────────
1. 10 features (6 raw + 4 engineered)
   Raw sensors alone miss cross-sensor anomalies: engine_temp=102°C is fine at
   RPM=2800 (full load) but overheating at RPM=700 (idle). The engineered ratio
   temp_per_rpm catches exactly this relationship.

2. Two-algorithm ensemble
   • Isolation Forest  — global outlier detector, primary scorer.
   • Local Outlier Factor (novelty=True) — density-based confirmation layer:
     if LOF also flags the reading, ensemble confidence = HIGH.

3. Training window — recent, clean, and before the scoring window
   The model trains on the most recent ANOMALY_TRAINING_SAMPLES readings that
   (a) precede the batch being scored and (b) did not themselves raise an alert.
   Excluding alerted readings keeps known faults out of the "normal" baseline.
   (The previous implementation trained on the *oldest* 200 rows forever.)

4. Model registry (model_versions table) + Redis cache
   Every trained bundle is stored, HMAC-signed, in PostgreSQL with its training
   window and per-feature statistics. Redis only caches it. Retraining happens
   when enough new clean data has accumulated or the recent distribution has
   drifted (PSI), and never more often than ANOMALY_RETRAIN_MIN_INTERVAL_MINUTES.

5. Pinned baselines
   Operators can pin a known-good model. A pinned model is never replaced
   automatically, so a slowly degrading vehicle cannot teach the detector that
   its own degradation is normal.

6. Class models for cold start
   A brand-new vehicle has no history. It is scored with a CLASS model trained
   on the other vehicles of the same device_type in the organization until it
   has enough readings for its own model.

7. Z-score feature contributions and per-device alert cooldown (unchanged).
"""

import io
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.metrics import ALERTS_CREATED, MODEL_RETRAINS
from app.core.security import sign_blob, verify_blob
from app.core.utils import as_utc, utcnow
from app.models.alert import Alert, AlertSeverity, FaultConfidence, FaultType
from app.models.device import Device
from app.models.ml import ModelVersion
from app.models.telemetry import Telemetry

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Normal operating ranges (used for threshold-based attribution and tests) ───

NORMAL_RANGES: dict[str, tuple[float, float]] = {
    "engine_temp":      (40.0,  105.0),
    "rpm":              (600.0, 3000.0),
    "fuel_level":       (5.0,   100.0),
    "battery_voltage":  (11.5,  14.8),
    "speed":            (0.0,   200.0),
    "vibration":        (0.0,   10.0),
}


def _identify_affected_metrics(record: Any) -> dict[str, Any]:
    """Return raw sensor fields that fall outside NORMAL_RANGES."""
    result: dict[str, Any] = {}
    for feature, (lo, hi) in NORMAL_RANGES.items():
        value = getattr(record, feature, None)
        if value is not None and not (lo <= value <= hi):
            result[feature] = {"value": value, "normal_range": [lo, hi]}
    return result


# ── Feature definitions ────────────────────────────────────────────────────────

RAW_FEATURES = ["engine_temp", "rpm", "fuel_level", "battery_voltage", "speed", "vibration"]

ENG_FEATURE_NAMES = [
    "temp_per_rpm",       # thermal stress per unit RPM — overheating under low load
    "vib_per_speed",      # vibration intensity relative to motion — wheel/bearing faults
    "engine_stress",      # normalised combined thermal × mechanical load
    "electrical_load",    # battery output proxy — detects alternator / drain faults
]

ALL_FEATURES = RAW_FEATURES + ENG_FEATURE_NAMES

_CACHE_KEY = "vw:anomaly:model:{version_id}"
_CACHE_TTL = 3600
_RETRAIN_FLAG_KEY = "vw:anomaly:retrain:{device_id}"
# Minimum seconds between two alerts for the same device (deduplication)
DEDUPE_WINDOW_SECS = 120
# Upper bound on readings scored per device per cycle (protects a cycle after long downtime)
MAX_SCORE_BATCH = 5000
_PSI_BINS = 10
_MIN_PSI_SAMPLES = 50


# ── Feature engineering ────────────────────────────────────────────────────────

def _extract_features(records: list[Telemetry]) -> np.ndarray:
    """Build a (N, 10) feature matrix from raw telemetry records."""
    rows = []
    for r in records:
        rpm_safe = max(r.rpm, 1.0)
        speed_safe = max(r.speed, 0.1)
        rows.append([
            r.engine_temp,
            r.rpm,
            r.fuel_level,
            r.battery_voltage,
            r.speed,
            r.vibration,
            r.engine_temp / rpm_safe * 1000,
            r.vibration / speed_safe,
            (r.engine_temp / 90.0) * (rpm_safe / 1500.0),
            r.battery_voltage * r.speed / 100.0,
        ])
    return np.array(rows, dtype=float).reshape(-1, len(ALL_FEATURES))


# ── Model training ─────────────────────────────────────────────────────────────

class _ModelBundle:
    """Container for the full fitted pipeline."""

    def __init__(
        self,
        iso: IsolationForest,
        lof: LocalOutlierFactor,
        scaler: StandardScaler,
        means: np.ndarray,
        stds: np.ndarray,
        n_train: int,
        bin_edges: list[np.ndarray] | None = None,
        bin_props: list[np.ndarray] | None = None,
    ) -> None:
        self.iso = iso
        self.lof = lof
        self.scaler = scaler
        self.means = means
        self.stds = stds
        self.n_train = n_train
        self.bin_edges = bin_edges or []
        self.bin_props = bin_props or []


def _histogram_props(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(values, bins=edges)
    props = counts / max(1, counts.sum())
    return np.clip(props, 1e-4, None)


def _train(X_raw: np.ndarray) -> _ModelBundle:
    """Fit scaler → IsoForest + LOF, and record the distribution for drift checks."""
    scaler = StandardScaler()
    X = scaler.fit_transform(X_raw)
    means = X_raw.mean(axis=0)
    stds = X_raw.std(axis=0) + 1e-8

    n_neighbours = min(20, max(5, len(X_raw) // 10))
    n_neighbours = min(n_neighbours, max(1, len(X_raw) - 1))

    iso = IsolationForest(n_estimators=150, contamination=0.05, max_samples="auto", random_state=42)
    iso.fit(X)

    lof = LocalOutlierFactor(n_neighbors=n_neighbours, contamination=0.05, novelty=True)
    lof.fit(X)

    # Quantile bins per raw feature — the reference distribution for PSI.
    bin_edges, bin_props = [], []
    for i in range(len(RAW_FEATURES)):
        edges = np.unique(np.quantile(X_raw[:, i], np.linspace(0, 1, _PSI_BINS + 1)))
        if len(edges) < 2:
            edges = np.array([X_raw[:, i].min() - 1e-6, X_raw[:, i].max() + 1e-6])
        edges[0], edges[-1] = -np.inf, np.inf
        bin_edges.append(edges)
        bin_props.append(_histogram_props(X_raw[:, i], edges))

    return _ModelBundle(iso, lof, scaler, means, stds, len(X_raw), bin_edges, bin_props)


def population_stability_index(bundle: _ModelBundle, X_raw: np.ndarray) -> float | None:
    """
    Mean PSI over the raw features: how far the recent distribution has moved
    from the training distribution. Rule of thumb: <0.1 stable, 0.1–0.25 some
    shift, >0.25 significant shift → retrain.
    """
    if not bundle.bin_edges or len(X_raw) < _MIN_PSI_SAMPLES:
        return None
    psis = []
    for i, (edges, expected) in enumerate(zip(bundle.bin_edges, bundle.bin_props)):
        actual = _histogram_props(X_raw[:, i], edges)
        psis.append(float(np.sum((actual - expected) * np.log(actual / expected))))
    return round(float(np.mean(psis)), 4)


def _serialize(bundle: _ModelBundle) -> bytes:
    buf = io.BytesIO()
    joblib.dump(bundle, buf, compress=3)
    return sign_blob(buf.getvalue())


def _deserialize(signed: bytes | None) -> _ModelBundle | None:
    """Verify the HMAC before unpickling — unsigned or tampered bytes are rejected."""
    if not signed:
        return None
    blob = verify_blob(signed)
    if blob is None:
        logger.warning("Rejected model artifact with an invalid signature")
        return None
    try:
        return joblib.load(io.BytesIO(blob))
    except Exception as exc:
        logger.warning("Could not deserialize model artifact: %s", exc)
        return None


def _feature_stats(bundle: _ModelBundle) -> dict[str, Any]:
    return {
        name: {"mean": round(float(bundle.means[i]), 4), "std": round(float(bundle.stds[i]), 4)}
        for i, name in enumerate(ALL_FEATURES)
    }


# ── Anomaly explanation ────────────────────────────────────────────────────────

def _feature_contributions(x_raw: np.ndarray, bundle: _ModelBundle) -> list[dict[str, Any]]:
    """Top-4 features by |z-score| against the training distribution."""
    z = (x_raw - bundle.means) / bundle.stds
    top_idx = np.argsort(np.abs(z))[::-1][:4]
    return [
        {
            "feature":    ALL_FEATURES[i],
            "z_score":    round(float(z[i]), 2),
            "value":      round(float(x_raw[i]), 3),
            "train_mean": round(float(bundle.means[i]), 3),
            "train_std":  round(float(bundle.stds[i]), 3),
            "direction":  "above" if z[i] > 0 else "below",
        }
        for i in top_idx
    ]


def _ensemble_score(bundle: _ModelBundle, X_raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = bundle.scaler.transform(X_raw)
    iso_scores = bundle.iso.score_samples(X)
    # LOF score_samples with novelty=True returns negative LOF scores; < -1.5 ⇒ local outlier.
    lof_flags = bundle.lof.score_samples(X) < -1.5
    return iso_scores, lof_flags, iso_scores


def _score_to_severity(score: float) -> AlertSeverity:
    if score < settings.anomaly_score_critical:
        return AlertSeverity.CRITICAL
    if score < settings.anomaly_score_medium:
        return AlertSeverity.MEDIUM
    return AlertSeverity.LOW


# ── Fault classification taxonomy ─────────────────────────────────────────────

_FAULT_THRESHOLDS = {
    # COOLANT_LEAK
    "engine_temp_medium": 110.0,
    "engine_temp_high":   120.0,
    "coolant_level_low":   50.0,   # % — with elevated temperature
    "coolant_level_crit":  30.0,
    # BATTERY_FAILURE
    "voltage_medium":      11.8,
    "voltage_high":        11.5,
    # TRANSMISSION_STRESS — RPM over-rev at speed
    "rpm_slip":           3200.0,
    "rpm_slip_high":      4000.0,
    "speed_load":           60.0,
    # WHEEL_BEARING — vibration at speed with normal engine temperature
    "vib_medium":            6.0,
    "vib_high":              8.0,
    "speed_vib":            60.0,
    # ENGINE_STRESS — RPM + temp conjunction
    "es_rpm":             3500.0,
    "es_temp":             100.0,
    "es_rpm_high":        4000.0,
    "es_temp_high":        110.0,
    # LOW_OIL_PRESSURE (psi) — only meaningful with the engine running
    "oil_low":              20.0,
    "oil_crit":             10.0,
    "oil_min_rpm":         900.0,
    # TIRE_PRESSURE (psi) — calibrated for heavy trucks (nominal 100–110 psi)
    "tire_low":             85.0,
    "tire_crit":            70.0,
}

# OBD-II diagnostic trouble codes → fault type. A reported code is direct
# evidence from the ECU, so it outranks sensor-pattern rules.
_DTC_EXACT: dict[str, FaultType] = {
    **{c: FaultType.COOLANT_LEAK for c in ("P0115", "P0116", "P0117", "P0118", "P0125", "P0128", "P0217", "P0218")},
    **{c: FaultType.BATTERY_FAILURE for c in ("P0560", "P0561", "P0562", "P0563", "P0620", "P0621", "P0622")},
    **{c: FaultType.LOW_OIL_PRESSURE for c in ("P0520", "P0521", "P0522", "P0523", "P0524")},
    **{c: FaultType.ENGINE_STRESS for c in ("P0219", "P0300", "P0301", "P0302", "P0303", "P0304", "P0305", "P0306")},
}


def classify_dtc(code: str) -> FaultType | None:
    code = code.upper()
    if code in _DTC_EXACT:
        return _DTC_EXACT[code]
    if len(code) == 5 and code[1:].isdigit():
        n = int(code[1:])
        if code[0] == "P" and 700 <= n <= 799:
            return FaultType.TRANSMISSION_STRESS
        if code[0] == "C":
            if 750 <= n <= 799:
                return FaultType.TIRE_PRESSURE       # TPMS
            if 35 <= n <= 50:
                return FaultType.WHEEL_BEARING       # wheel speed sensor circuits
            return FaultType.BRAKE_WEAR              # other chassis/ABS codes
    return None


def fault_classifier(record: Telemetry) -> tuple[FaultType, FaultConfidence]:
    """
    Map a telemetry reading to a named fault type and confidence.

    Priority: ECU trouble codes → multi-sensor conjunctions → single-sensor rules.

        DTC codes           → the fault the ECU itself reported (HIGH)
        ENGINE_STRESS       → RPM + temp together
        LOW_OIL_PRESSURE    → oil pressure below floor with engine running
        COOLANT_LEAK        → temperature dominant / low coolant level
        TRANSMISSION_STRESS → RPM over-rev under load
        BATTERY_FAILURE     → voltage below safe floor
        TIRE_PRESSURE       → lowest tyre below floor
        WHEEL_BEARING       → vibration at speed with normal engine temperature
        UNKNOWN_ANOMALY     → fallback (MEDIUM confidence, always)
    """
    t = _FAULT_THRESHOLDS
    et = record.engine_temp
    rv = record.rpm
    bv = record.battery_voltage
    sp = record.speed
    vb = record.vibration
    oil = getattr(record, "oil_pressure", None)
    coolant = getattr(record, "coolant_level", None)
    tire = getattr(record, "tire_pressure", None)

    for code in getattr(record, "dtc_codes", None) or []:
        fault = classify_dtc(code)
        if fault:
            return fault, FaultConfidence.HIGH

    if rv > t["es_rpm"] and et > t["es_temp"]:
        confidence = (
            FaultConfidence.HIGH
            if rv > t["es_rpm_high"] and et > t["es_temp_high"]
            else FaultConfidence.MEDIUM
        )
        return FaultType.ENGINE_STRESS, confidence

    if oil is not None and rv > t["oil_min_rpm"] and oil < t["oil_low"]:
        return FaultType.LOW_OIL_PRESSURE, (
            FaultConfidence.HIGH if oil < t["oil_crit"] else FaultConfidence.MEDIUM
        )

    low_coolant = coolant is not None and coolant < t["coolant_level_low"] and et > t["es_temp"]
    if et > t["engine_temp_medium"] or low_coolant:
        high = et > t["engine_temp_high"] or (coolant is not None and coolant < t["coolant_level_crit"])
        return FaultType.COOLANT_LEAK, FaultConfidence.HIGH if high else FaultConfidence.MEDIUM

    if rv > t["rpm_slip"] and sp > t["speed_load"]:
        confidence = FaultConfidence.HIGH if rv > t["rpm_slip_high"] else FaultConfidence.MEDIUM
        return FaultType.TRANSMISSION_STRESS, confidence

    if bv < t["voltage_medium"]:
        confidence = FaultConfidence.HIGH if bv < t["voltage_high"] else FaultConfidence.MEDIUM
        return FaultType.BATTERY_FAILURE, confidence

    if tire is not None and tire < t["tire_low"]:
        return FaultType.TIRE_PRESSURE, (
            FaultConfidence.HIGH if tire < t["tire_crit"] else FaultConfidence.MEDIUM
        )

    # Vibration that appears only at speed, with a normal thermal pattern, isolates
    # the fault to the wheel end (bearing / hub) rather than the engine.
    if vb > t["vib_medium"] and sp > t["speed_vib"] and et <= t["engine_temp_medium"]:
        confidence = FaultConfidence.HIGH if vb > t["vib_high"] else FaultConfidence.MEDIUM
        return FaultType.WHEEL_BEARING, confidence

    return FaultType.UNKNOWN_ANOMALY, FaultConfidence.MEDIUM


# ── Main service ───────────────────────────────────────────────────────────────

def _clean_telemetry(device_ids: list[uuid.UUID]):
    """Telemetry for the given devices that did not raise an alert."""
    return (
        select(Telemetry)
        .outerjoin(Alert, Alert.telemetry_id == Telemetry.id)
        .where(Telemetry.device_id.in_(device_ids))
        .where(Alert.id.is_(None))
    )


class AnomalyService:
    def __init__(self, db: AsyncSession, redis: Any = None) -> None:
        self._db = db
        self._redis = redis

    # ── Data access ──────────────────────────────────────────────────────────

    async def _training_records(
        self, device_ids: list[uuid.UUID], before: datetime | None, limit: int
    ) -> list[Telemetry]:
        stmt = _clean_telemetry(device_ids)
        if before is not None:
            stmt = stmt.where(Telemetry.recorded_at < before)
        stmt = stmt.order_by(Telemetry.recorded_at.desc()).limit(limit)
        records = list((await self._db.execute(stmt)).scalars().all())
        records.reverse()
        return records

    async def _unscored_records(self, device_id: uuid.UUID, after: datetime) -> list[Telemetry]:
        stmt = (
            _clean_telemetry([device_id])
            .where(Telemetry.recorded_at > after)
            .order_by(Telemetry.recorded_at.asc())
            .limit(MAX_SCORE_BATCH)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def _count_clean_since(
        self, device_ids: list[uuid.UUID], after: datetime | None, before: datetime | None = None
    ) -> int:
        """Clean readings newer than `after` — and, when given, older than `before` (already scored)."""
        sub = _clean_telemetry(device_ids)
        if after is not None:
            sub = sub.where(Telemetry.recorded_at > after)
        if before is not None:
            sub = sub.where(Telemetry.recorded_at < before)
        return (await self._db.execute(select(func.count()).select_from(sub.subquery()))).scalar_one()

    async def _last_alert_time(self, device_id: uuid.UUID) -> datetime | None:
        result = await self._db.execute(
            select(Alert.created_at)
            .where(Alert.device_id == device_id)
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ── Model registry ───────────────────────────────────────────────────────

    async def _active_version(
        self, org_id: uuid.UUID, scope: str, device_id: uuid.UUID | None, device_type: str
    ) -> ModelVersion | None:
        stmt = select(ModelVersion).where(
            ModelVersion.organization_id == org_id,
            ModelVersion.scope == scope,
            ModelVersion.is_active.is_(True),
        )
        if scope == "DEVICE":
            stmt = stmt.where(ModelVersion.device_id == device_id)
        else:
            stmt = stmt.where(ModelVersion.device_type == device_type)
        stmt = stmt.order_by(ModelVersion.pinned.desc(), ModelVersion.version.desc()).limit(1)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def _load_bundle(self, version: ModelVersion) -> _ModelBundle | None:
        key = _CACHE_KEY.format(version_id=version.id)
        if self._redis is not None:
            try:
                cached = await self._redis.get(key)
                bundle = _deserialize(cached) if cached else None
                if bundle is not None:
                    return bundle
            except Exception:
                logger.debug("Model cache read failed for %s", version.id)

        artifact = (
            await self._db.execute(
                select(ModelVersion.artifact).where(ModelVersion.id == version.id)
            )
        ).scalar_one_or_none()
        bundle = _deserialize(artifact)
        if bundle is not None and self._redis is not None and artifact:
            try:
                await self._redis.set(key, artifact, ex=_CACHE_TTL)
            except Exception:
                pass
        return bundle

    async def _register(
        self,
        bundle: _ModelBundle,
        records: list[Telemetry],
        *,
        org_id: uuid.UUID,
        scope: str,
        device_id: uuid.UUID | None,
        device_type: str,
        reason: str,
        drift_psi: float | None,
    ) -> ModelVersion:
        prev_filter = [
            ModelVersion.organization_id == org_id,
            ModelVersion.scope == scope,
            (ModelVersion.device_id == device_id) if scope == "DEVICE" else (ModelVersion.device_type == device_type),
        ]
        latest = (
            await self._db.execute(select(func.max(ModelVersion.version)).where(*prev_filter))
        ).scalar_one_or_none() or 0

        await self._db.execute(
            update(ModelVersion).where(*prev_filter).values(is_active=False)
            .execution_options(synchronize_session=False)
        )

        artifact = _serialize(bundle)
        version = ModelVersion(
            organization_id=org_id,
            scope=scope,
            device_id=device_id,
            device_type=device_type,
            version=latest + 1,
            reason=reason,
            n_train=bundle.n_train,
            window_start=records[0].recorded_at if records else None,
            window_end=records[-1].recorded_at if records else None,
            feature_stats=_feature_stats(bundle),
            drift_psi=drift_psi,
            is_active=True,
            pinned=False,
            artifact=artifact,
        )
        self._db.add(version)
        await self._db.flush()

        # Keep artifacts for the newest N unpinned versions only (metadata is kept forever).
        keep = settings.anomaly_models_kept_per_device
        stale = (
            await self._db.execute(
                select(ModelVersion.id)
                .where(*prev_filter, ModelVersion.pinned.is_(False), ModelVersion.version <= latest + 1 - keep)
            )
        ).scalars().all()
        if stale:
            await self._db.execute(
                update(ModelVersion).where(ModelVersion.id.in_(stale)).values(artifact=None)
                .execution_options(synchronize_session=False)
            )

        if self._redis is not None:
            try:
                await self._redis.set(_CACHE_KEY.format(version_id=version.id), artifact, ex=_CACHE_TTL)
            except Exception:
                pass

        MODEL_RETRAINS.labels(reason, scope).inc()
        logger.info(
            "Anomaly model v%d registered (scope=%s device=%s type=%s reason=%s n_train=%d psi=%s)",
            version.version, scope, device_id, device_type, reason, bundle.n_train, drift_psi,
        )
        return version

    async def _manual_retrain_requested(self, device_id: uuid.UUID) -> bool:
        if self._redis is None:
            return False
        try:
            flag = await self._redis.get(_RETRAIN_FLAG_KEY.format(device_id=device_id))
            if flag:
                await self._redis.delete(_RETRAIN_FLAG_KEY.format(device_id=device_id))
                return True
        except Exception:
            pass
        return False

    async def _device_model(
        self, device: Device, scoring_start: datetime
    ) -> tuple[_ModelBundle | None, ModelVersion | None]:
        """Return the model to score this device with, training or retraining if needed."""
        version = await self._active_version(device.organization_id, "DEVICE", device.id, device.device_type)
        bundle = await self._load_bundle(version) if version else None
        manual = await self._manual_retrain_requested(device.id)

        if bundle is not None and version is not None and version.pinned and not manual:
            return bundle, version

        reason: str | None = None
        drift: float | None = None
        if bundle is None:
            reason = "INITIAL" if version is None else "SCHEDULED"  # artifact lost → rebuild
        elif manual:
            reason = "MANUAL"
        elif bundle.n_train < settings.anomaly_training_samples and (
            await self._count_clean_since([device.id], as_utc(version.window_end), scoring_start)
            >= max(bundle.n_train, settings.anomaly_min_training_samples)
        ):
            # Growth phase: a young model trained on a few minutes of data (often a
            # cold start — engine warming up, truck parked) makes everything after it
            # look anomalous. Retrain whenever the clean history has doubled, without
            # waiting for the hourly limit, until the full training window is reached.
            reason = "GROWTH"
        else:
            trained_at = as_utc(version.trained_at)
            if utcnow() - trained_at >= timedelta(minutes=settings.anomaly_retrain_min_interval_minutes):
                new_clean = await self._count_clean_since([device.id], as_utc(version.window_end), scoring_start)
                recent = await self._training_records([device.id], scoring_start, settings.anomaly_training_samples)
                drift = population_stability_index(bundle, _extract_features(recent)) if recent else None
                if drift is not None and drift > settings.anomaly_drift_psi_threshold:
                    reason = "DRIFT"
                elif new_clean >= settings.anomaly_retrain_min_new_records:
                    reason = "SCHEDULED"

        if reason is None:
            return bundle, version

        records = await self._training_records([device.id], scoring_start, settings.anomaly_training_samples)
        if len(records) < settings.anomaly_min_training_samples:
            return bundle, version  # keep whatever we had (may be None → class model)

        new_bundle = _train(_extract_features(records))
        if drift is None and bundle is not None and reason != "INITIAL":
            drift = population_stability_index(bundle, _extract_features(records))
        new_version = await self._register(
            new_bundle, records,
            org_id=device.organization_id, scope="DEVICE", device_id=device.id,
            device_type=device.device_type, reason=reason, drift_psi=drift,
        )
        return new_bundle, new_version

    async def _class_model(self, device: Device) -> tuple[_ModelBundle | None, ModelVersion | None]:
        """Cold-start model shared by all vehicles of the same type in the organization."""
        version = await self._active_version(device.organization_id, "CLASS", None, device.device_type)
        bundle = await self._load_bundle(version) if version else None
        fresh = version is not None and utcnow() - as_utc(version.trained_at) < timedelta(
            minutes=settings.anomaly_retrain_min_interval_minutes
        )
        if bundle is not None and (fresh or version.pinned):
            return bundle, version

        peer_ids = (
            await self._db.execute(
                select(Device.id).where(
                    Device.organization_id == device.organization_id,
                    Device.device_type == device.device_type,
                    Device.id != device.id,
                )
            )
        ).scalars().all()
        if not peer_ids:
            return bundle, version
        records = await self._training_records(list(peer_ids), None, settings.anomaly_training_samples * 3)
        if len(records) < settings.anomaly_training_samples // 2:
            return bundle, version
        new_bundle = _train(_extract_features(records))
        new_version = await self._register(
            new_bundle, records,
            org_id=device.organization_id, scope="CLASS", device_id=None,
            device_type=device.device_type, reason="INITIAL" if version is None else "SCHEDULED",
            drift_psi=None,
        )
        return new_bundle, new_version

    # ── Scoring ──────────────────────────────────────────────────────────────

    async def run_for_device(self, device_id: uuid.UUID, since: datetime | None = None) -> list[Alert]:
        device = await self._db.get(Device, device_id)
        if device is None:
            return []

        now = utcnow()
        scoring_start = (
            as_utc(since)
            or as_utc(device.anomaly_watermark)
            or now - timedelta(minutes=settings.anomaly_backfill_minutes)
        )

        new_records = await self._unscored_records(device.id, scoring_start)
        if not new_records:
            return []

        bundle, version = await self._device_model(device, scoring_start)
        if bundle is None:
            bundle, version = await self._class_model(device)
        if bundle is None:
            # Bootstrap: a brand-new fleet with no history anywhere. The first batch
            # becomes the baseline. It is NOT scored: a model scoring the very data it
            # was trained on flags ~contamination (5 %) of it by construction, so every
            # new vehicle — healthy or not — would raise an alert on day one.
            records = await self._training_records([device.id], None, settings.anomaly_training_samples)
            if len(records) < settings.anomaly_min_training_samples:
                return []
            await self._register(
                _train(_extract_features(records)), records,
                org_id=device.organization_id, scope="DEVICE", device_id=device.id,
                device_type=device.device_type, reason="INITIAL", drift_psi=None,
            )
            device.anomaly_watermark = as_utc(new_records[-1].recorded_at)
            return []

        X_new = _extract_features(new_records)
        iso_scores, lof_flags, _ = _ensemble_score(bundle, X_new)

        last_alert_ts = as_utc(await self._last_alert_time(device.id))
        cooldown_until = last_alert_ts + timedelta(seconds=DEDUPE_WINDOW_SECS) if last_alert_ts else None

        created_alerts: list[Alert] = []
        for record, iso_score, lof_flagged, x_raw in zip(new_records, iso_scores, lof_flags, X_new):
            if iso_score >= settings.anomaly_score_low:
                continue  # not anomalous
            if cooldown_until and as_utc(record.recorded_at) <= cooldown_until:
                continue

            severity = _score_to_severity(float(iso_score))
            fault_type, fault_confidence = fault_classifier(record)

            affected_metrics: dict[str, Any] = {
                "top_contributors": _feature_contributions(x_raw, bundle),
                "ensemble": {
                    "isolation_forest_score": round(float(iso_score), 4),
                    "lof_confirmed":          bool(lof_flagged),
                    "confidence":             "HIGH" if lof_flagged else "MEDIUM",
                    "n_features":             len(ALL_FEATURES),
                    "n_train_samples":        bundle.n_train,
                    "model_version":          version.version if version else None,
                    "model_scope":            version.scope if version else None,
                },
            }
            if record.dtc_codes:
                affected_metrics["dtc_codes"] = record.dtc_codes

            alert = Alert(
                device_id=device.id,
                telemetry_id=record.id,
                severity=severity,
                anomaly_score=float(iso_score),
                affected_metrics=affected_metrics,
                fault_type=fault_type,
                fault_confidence=fault_confidence,
                # The event time is the reading's time — backfilled data must not
                # look like it happened just now.
                created_at=as_utc(record.recorded_at),
            )
            self._db.add(alert)
            created_alerts.append(alert)
            ALERTS_CREATED.labels(severity.value, fault_type.value).inc()
            cooldown_until = as_utc(record.recorded_at) + timedelta(seconds=DEDUPE_WINDOW_SECS)

        device.anomaly_watermark = as_utc(new_records[-1].recorded_at)

        if created_alerts:
            await self._db.flush()
            for alert in created_alerts:
                await self._db.refresh(alert)
        return created_alerts


async def request_retrain(redis: Any, device_id: uuid.UUID) -> None:
    """Flag a device for retraining on the next worker cycle."""
    await redis.set(_RETRAIN_FLAG_KEY.format(device_id=device_id), b"1", ex=24 * 3600)

