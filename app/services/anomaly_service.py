"""
Ensemble Anomaly Detection Pipeline — Isolation Forest + Local Outlier Factor

Architecture decisions
──────────────────────
1. 10 features (6 raw + 4 engineered)
   Raw sensors alone miss cross-sensor anomalies: engine_temp=102°C is fine at
   RPM=2800 (full load) but overheating at RPM=700 (idle). The engineered ratio
   temp_per_rpm catches exactly this relationship.

2. Two-algorithm ensemble
   • Isolation Forest  — global outlier detector, O(n log n), handles sparse
     high-dimensional data well. Primary scorer.
   • Local Outlier Factor (novelty=True) — density-based, catches local clusters
     that appear normal globally but are anomalous within their neighbourhood.
     Acts as a confirmation layer: if LOF also flags the reading, confidence = HIGH.

3. StandardScaler normalisation
   Both models are trained on scaled features so that battery_voltage (≈12–14)
   and RPM (700–2800) contribute equally without RPM dominating by magnitude.

4. Redis model cache (TTL = 30 min)
   Re-fitting on every 60-second cycle is wasteful. The fitted {IsoForest, LOF,
   Scaler, training stats} bundle is pickled to Redis. Cache is invalidated when
   the training corpus grows by ≥ RETRAIN_THRESHOLD new records, ensuring the
   model stays fresh without retraining every cycle.

5. Z-score feature contributions
   After the ensemble flags an anomaly, we compute per-feature z-scores against
   the training distribution. This answers "WHY is this anomalous?" with a
   quantitative answer: "engine_temp is 4.2σ above its training mean" — far more
   informative than a plain threshold check.

6. Alert deduplication (per-device cooldown)
   A single mechanical failure can produce hundreds of anomalous readings. We
   enforce a minimum gap between consecutive alerts per device (default 2 min)
   so the alert queue reflects distinct *events*, not a flood of readings.
"""

import logging
import pickle
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.alert import Alert, AlertSeverity, FaultConfidence, FaultType
from app.models.telemetry import Telemetry

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Normal operating ranges (used for threshold-based attribution and tests) ───
#
# These represent the expected healthy envelope for each raw sensor.
# They are intentionally conservative — values outside these bounds are
# strong candidates for contributing to an anomaly, though the ML models
# (IsolationForest + LOF) use statistical training distributions rather than
# these hard limits for their primary scoring.

NORMAL_RANGES: dict[str, tuple[float, float]] = {
    "engine_temp":      (40.0,  105.0),
    "rpm":              (600.0, 3000.0),
    "fuel_level":       (5.0,   100.0),
    "battery_voltage":  (11.5,  14.8),
    "speed":            (0.0,   200.0),
    "vibration":        (0.0,   10.0),
}


def _identify_affected_metrics(record: Any) -> dict[str, Any]:
    """
    Return a flat dict of raw sensor fields that fall outside NORMAL_RANGES.

    Each entry has the shape expected by GeminiService._build_prompt (legacy
    flat format) and is also used in unit tests to verify threshold logic
    independently of the trained ML models.

    Example return value::

        {
            "engine_temp": {"value": 145.0, "normal_range": [40.0, 105.0]},
        }
    """
    result: dict[str, Any] = {}
    for feature, (lo, hi) in NORMAL_RANGES.items():
        value = getattr(record, feature, None)
        if value is not None and not (lo <= value <= hi):
            result[feature] = {
                "value": value,
                "normal_range": [lo, hi],
            }
    return result


# ── Feature definitions ────────────────────────────────────────────────────────

RAW_FEATURES = [
    "engine_temp",
    "rpm",
    "fuel_level",
    "battery_voltage",
    "speed",
    "vibration",
]

# Engineered features capture cross-sensor relationships that single-sensor
# thresholds can never detect.
ENG_FEATURE_NAMES = [
    "temp_per_rpm",       # thermal stress per unit RPM — overheating under low load
    "vib_per_speed",      # vibration intensity relative to motion — wheel/bearing faults
    "engine_stress",      # normalised combined thermal × mechanical load
    "electrical_load",    # battery output proxy — detects alternator / drain faults
]

ALL_FEATURES = RAW_FEATURES + ENG_FEATURE_NAMES

# Redis keys
_CACHE_KEY    = "vw:anomaly:model:{device_id}"
_CACHE_TTL    = 1800  # 30 minutes
# Retrain when training corpus has grown by this many records since last fit
RETRAIN_THRESHOLD   = 50
# Minimum seconds between two alerts for the same device (deduplication)
DEDUPE_WINDOW_SECS  = 120


# ── Feature engineering ────────────────────────────────────────────────────────

def _extract_features(records: list[Telemetry]) -> np.ndarray:
    """
    Build a (N, 10) feature matrix from raw telemetry records.

    The 4 engineered features encode domain knowledge that is invisible to any
    per-sensor threshold check but immediately learnable by tree-based models:

    temp_per_rpm    — engine_temp / RPM×1000
        Captures thermal load relative to mechanical load.
        A value of 0.04 at RPM=2500 is fine; the same value at RPM=700 indicates
        the engine is running hot while barely working.

    vib_per_speed   — vibration / speed
        Vibration at highway speed is partly road-induced (normal).
        The same vibration level while barely moving points to an internal fault.

    engine_stress   — (temp / 90) × (rpm / 1500)
        A single unitless stress index. Stays near 1.0 under healthy operation;
        deviates sharply during anomalous events.

    electrical_load — battery_voltage × speed / 100
        Proxy for alternator output vs. electrical demand.
        A drop here while speed is high suggests alternator degradation.
    """
    rows = []
    for r in records:
        rpm_safe   = max(r.rpm,   1.0)
        speed_safe = max(r.speed, 0.1)

        rows.append([
            r.engine_temp,
            r.rpm,
            r.fuel_level,
            r.battery_voltage,
            r.speed,
            r.vibration,
            # engineered
            r.engine_temp / rpm_safe * 1000,
            r.vibration   / speed_safe,
            (r.engine_temp / 90.0) * (rpm_safe / 1500.0),
            r.battery_voltage * r.speed / 100.0,
        ])
    return np.array(rows, dtype=float)


# ── Model training ─────────────────────────────────────────────────────────────

class _ModelBundle:
    """Container for the full fitted pipeline stored in Redis."""

    def __init__(
        self,
        iso:     IsolationForest,
        lof:     LocalOutlierFactor,
        scaler:  StandardScaler,
        means:   np.ndarray,
        stds:    np.ndarray,
        n_train: int,
    ) -> None:
        self.iso     = iso
        self.lof     = lof
        self.scaler  = scaler
        self.means   = means
        self.stds    = stds
        self.n_train = n_train


def _train(X_raw: np.ndarray) -> _ModelBundle:
    """Fit scaler → IsoForest + LOF on raw feature matrix."""
    scaler  = StandardScaler()
    X       = scaler.fit_transform(X_raw)
    means   = X_raw.mean(axis=0)
    stds    = X_raw.std(axis=0) + 1e-8

    n_neighbours = min(20, max(5, len(X_raw) // 10))

    iso = IsolationForest(
        n_estimators=150,
        contamination=0.05,
        max_samples="auto",
        random_state=42,
    )
    iso.fit(X)

    lof = LocalOutlierFactor(
        n_neighbors=n_neighbours,
        contamination=0.05,
        novelty=True,
    )
    lof.fit(X)

    return _ModelBundle(iso, lof, scaler, means, stds, len(X_raw))


# ── Anomaly explanation ────────────────────────────────────────────────────────

def _feature_contributions(
    x_raw: np.ndarray, bundle: _ModelBundle
) -> list[dict[str, Any]]:
    """
    Z-score each feature against the training distribution.

    Returns the top-4 contributors sorted by |z-score|, giving a quantitative
    answer to "WHY was this reading flagged?":
        engine_temp: 4.2σ above training mean (value=142.3, mean=87.4)
    """
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


def _ensemble_score(
    bundle: _ModelBundle, X_raw: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Score new observations with both models.

    Returns
    -------
    iso_scores : IsolationForest anomaly scores  (negative = anomalous)
    lof_flags  : bool array — True when LOF also considers the point an outlier
    combined   : primary score used for thresholding (IsoForest-led)
    """
    X = bundle.scaler.transform(X_raw)

    iso_scores = bundle.iso.score_samples(X)

    # LOF score_samples with novelty=True returns negative LOF scores.
    # Values < -1.5 (i.e. LOF > 1.5) are considered local outliers.
    lof_scores = bundle.lof.score_samples(X)
    lof_flags  = lof_scores < -1.5

    return iso_scores, lof_flags, iso_scores   # combined = IsoForest primary


def _score_to_severity(score: float) -> AlertSeverity:
    if score < settings.anomaly_score_critical:
        return AlertSeverity.CRITICAL
    if score < settings.anomaly_score_medium:
        return AlertSeverity.MEDIUM
    return AlertSeverity.LOW


# ── Fault classification taxonomy ─────────────────────────────────────────────

# Thresholds mirror the simulator's fault personalities so that the classifier
# fires on exactly the readings the simulator injects.
_FAULT_THRESHOLDS = {
    # COOLANT_LEAK
    "engine_temp_medium": 110.0,   # °C — start of fault window
    "engine_temp_high":   120.0,   # °C — HIGH confidence
    # BATTERY_FAILURE
    "voltage_medium":      11.8,   # V — start of fault window
    "voltage_high":        11.5,   # V — HIGH confidence (lower = worse)
    # TRANSMISSION_STRESS — RPM over-rev at speed
    "rpm_slip":           3200.0,  # RPM — slip threshold
    "rpm_slip_high":      4000.0,  # RPM — HIGH confidence
    "speed_load":           60.0,  # km/h — under-load qualifier
    # BRAKE_WEAR — vibration at speed
    "vib_medium":            6.0,  # g — start of fault window
    "vib_high":              8.0,  # g — HIGH confidence
    "speed_vib":            60.0,  # km/h — fault only meaningful above this
    # ENGINE_STRESS — RPM + temp conjunction
    "es_rpm":             3500.0,  # RPM
    "es_temp":             100.0,  # °C
    "es_rpm_high":        4000.0,  # RPM — "significantly above" qualifier
    "es_temp_high":        110.0,  # °C — "significantly above" qualifier
}


def fault_classifier(record: Telemetry) -> tuple[FaultType, FaultConfidence]:
    """
    Map a single telemetry reading to a named fault type and confidence level.

    Rules are evaluated in priority order, most specific (multi-sensor
    conjunctions) first so that overlapping patterns resolve predictably:

        ENGINE_STRESS       → RPM + temp together (highest specificity)
        COOLANT_LEAK        → temperature dominant
        TRANSMISSION_STRESS → RPM over-rev under load
        BATTERY_FAILURE     → voltage below safe floor
        BRAKE_WEAR          → vibration at speed with normal temp
        UNKNOWN_ANOMALY     → fallback (MEDIUM confidence, always)

    Returns (FaultType, FaultConfidence).
    """
    t  = _FAULT_THRESHOLDS
    et = record.engine_temp
    rv = record.rpm
    bv = record.battery_voltage
    sp = record.speed
    vb = record.vibration

    # ── ENGINE_STRESS — multi-sensor conjunction (check first) ───────────────
    # RPM over-rev at the same time as thermal overload indicates the engine
    # is being pushed beyond its design envelope on both axes simultaneously.
    if rv > t["es_rpm"] and et > t["es_temp"]:
        confidence = (
            FaultConfidence.HIGH
            if rv > t["es_rpm_high"] and et > t["es_temp_high"]
            else FaultConfidence.MEDIUM
        )
        return FaultType.ENGINE_STRESS, confidence

    # ── COOLANT_LEAK — temperature dominant ──────────────────────────────────
    # Sustained engine temperature above 110°C without proportional RPM
    # increase (which would be ENGINE_STRESS) points to cooling system failure.
    if et > t["engine_temp_medium"]:
        confidence = (
            FaultConfidence.HIGH if et > t["engine_temp_high"] else FaultConfidence.MEDIUM
        )
        return FaultType.COOLANT_LEAK, confidence

    # ── TRANSMISSION_STRESS — RPM over-rev under load ─────────────────────
    # High RPM while the vehicle is at speed signals the transmission is
    # slipping — engine spins up but gear ratio is lost under load.
    if rv > t["rpm_slip"] and sp > t["speed_load"]:
        confidence = (
            FaultConfidence.HIGH if rv > t["rpm_slip_high"] else FaultConfidence.MEDIUM
        )
        return FaultType.TRANSMISSION_STRESS, confidence

    # ── BATTERY_FAILURE — voltage below safe floor ────────────────────────
    if bv < t["voltage_medium"]:
        confidence = (
            FaultConfidence.HIGH if bv < t["voltage_high"] else FaultConfidence.MEDIUM
        )
        return FaultType.BATTERY_FAILURE, confidence

    # ── BRAKE_WEAR — vibration at speed, thermal pattern normal ──────────
    # Elevated vibration only at speed (not stationary) with no engine temp
    # anomaly isolates the fault to the drivetrain / wheel end, not the engine.
    if vb > t["vib_medium"] and sp > t["speed_vib"] and et <= t["engine_temp_medium"]:
        confidence = (
            FaultConfidence.HIGH if vb > t["vib_high"] else FaultConfidence.MEDIUM
        )
        return FaultType.BRAKE_WEAR, confidence

    # ── UNKNOWN_ANOMALY ───────────────────────────────────────────────────────
    return FaultType.UNKNOWN_ANOMALY, FaultConfidence.MEDIUM


# ── Redis model cache ──────────────────────────────────────────────────────────

async def _load_bundle(redis: Any, device_id: uuid.UUID) -> _ModelBundle | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(_CACHE_KEY.format(device_id=device_id))
        if raw:
            return pickle.loads(raw)
    except Exception:
        logger.debug("Model cache miss for device %s", device_id)
    return None


async def _save_bundle(
    redis: Any, device_id: uuid.UUID, bundle: _ModelBundle
) -> None:
    if redis is None:
        return
    try:
        await redis.set(
            _CACHE_KEY.format(device_id=device_id),
            pickle.dumps(bundle),
            ex=_CACHE_TTL,
        )
    except Exception as exc:
        logger.debug("Failed to cache model for device %s: %s", device_id, exc)


# ── Main service ───────────────────────────────────────────────────────────────

class AnomalyService:
    def __init__(self, db: AsyncSession, redis: Any = None) -> None:
        self._db    = db
        self._redis = redis

    async def _get_training_data(self, device_id: uuid.UUID) -> list[Telemetry]:
        result = await self._db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.recorded_at.asc())
            .limit(settings.anomaly_training_samples)
        )
        return list(result.scalars().all())

    async def _get_unscored_records(
        self,
        device_id:      uuid.UUID,
        since:          datetime | None,
        exclude_before: datetime | None,
    ) -> list[Telemetry]:
        stmt = (
            select(Telemetry)
            .outerjoin(Alert, Alert.telemetry_id == Telemetry.id)
            .where(Telemetry.device_id == device_id)
            .where(Alert.id.is_(None))
        )
        if since:
            stmt = stmt.where(Telemetry.recorded_at > since)
        elif exclude_before:
            stmt = stmt.where(Telemetry.recorded_at > exclude_before)
        stmt = stmt.order_by(Telemetry.recorded_at.asc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def _last_alert_time(self, device_id: uuid.UUID) -> datetime | None:
        """Return timestamp of the most recent alert for deduplication."""
        result = await self._db.execute(
            select(Alert.created_at)
            .where(Alert.device_id == device_id)
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def run_for_device(
        self,
        device_id: uuid.UUID,
        since:     datetime | None = None,
    ) -> list[Alert]:
        # ── 1. Training data ──────────────────────────────────────────────────
        training_records = await self._get_training_data(device_id)
        if len(training_records) < 10:
            return []

        training_cutoff: datetime | None = None
        if not since and training_records:
            # Only exclude training records from scoring when we have a FULL training
            # set (>= ANOMALY_TRAINING_SAMPLES). If we have fewer records we use all
            # of them for training AND score them — contamination=0.05 means only
            # 5% get flagged, and per-device deduplication prevents alert storms.
            # Without this guard, new devices with < 200 records leave nothing to
            # score on the first cycle and never generate alerts.
            if len(training_records) >= settings.anomaly_training_samples:
                training_cutoff = training_records[-1].recorded_at

        # ── 2. Records to score ───────────────────────────────────────────────
        new_records = await self._get_unscored_records(
            device_id, since=since, exclude_before=training_cutoff
        )
        if not new_records:
            return []

        X_train = _extract_features(training_records)
        X_new   = _extract_features(new_records)

        # ── 3. Model — load from cache or retrain ─────────────────────────────
        bundle = await _load_bundle(self._redis, device_id)

        needs_retrain = (
            bundle is None
            or (len(training_records) - bundle.n_train) >= RETRAIN_THRESHOLD
        )
        if needs_retrain:
            bundle = _train(X_train)
            await _save_bundle(self._redis, device_id, bundle)
            logger.info(
                "Anomaly worker: retrained model for device %s "
                "(n_train=%d, features=%d)",
                device_id, bundle.n_train, len(ALL_FEATURES),
            )

        # ── 4. Score with ensemble ────────────────────────────────────────────
        iso_scores, lof_flags, _ = _ensemble_score(bundle, X_new)

        # ── 5. Deduplication — enforce per-device cooldown ───────────────────
        last_alert_ts = await self._last_alert_time(device_id)
        cooldown_until = (
            last_alert_ts + timedelta(seconds=DEDUPE_WINDOW_SECS)
            if last_alert_ts else None
        )

        created_alerts: list[Alert] = []

        for record, iso_score, lof_flagged in zip(new_records, iso_scores, lof_flags):
            if iso_score >= settings.anomaly_score_low:
                continue  # not anomalous

            # Deduplication: skip if within cooldown window.
            # Normalise both sides to tz-aware UTC before comparison —
            # PostgreSQL returns tz-aware datetimes but SQLite (used in CI)
            # returns naive ones, causing TypeError on mixed comparison.
            if cooldown_until:
                recorded_at = record.recorded_at
                if recorded_at.tzinfo is None:
                    recorded_at = recorded_at.replace(tzinfo=timezone.utc)
                cmp_cooldown = cooldown_until
                if cmp_cooldown.tzinfo is None:
                    cmp_cooldown = cmp_cooldown.replace(tzinfo=timezone.utc)
                if recorded_at <= cmp_cooldown:
                    continue

            severity = _score_to_severity(float(iso_score))

            # ── 6. Z-score feature contributions ─────────────────────────────
            x_raw = _extract_features([record])[0]
            contributions = _feature_contributions(x_raw, bundle)

            affected_metrics: dict[str, Any] = {
                "top_contributors": contributions,
                "ensemble": {
                    "isolation_forest_score": round(float(iso_score), 4),
                    "lof_confirmed":          bool(lof_flagged),
                    "confidence":             "HIGH" if lof_flagged else "MEDIUM",
                    "n_features":             len(ALL_FEATURES),
                    "n_train_samples":        bundle.n_train,
                },
            }

            # ── 7. Rule-based fault classification ───────────────────────────
            fault_type, fault_confidence = fault_classifier(record)
            logger.info(
                "Anomaly worker: device %s — fault_type=%s confidence=%s score=%.4f",
                device_id, fault_type.value, fault_confidence.value, iso_score,
            )

            alert = Alert(
                device_id=device_id,
                telemetry_id=record.id,
                severity=severity,
                anomaly_score=float(iso_score),
                affected_metrics=affected_metrics,
                fault_type=fault_type,
                fault_confidence=fault_confidence,
                created_at=datetime.now(timezone.utc),
            )
            self._db.add(alert)
            created_alerts.append(alert)

            # Advance cooldown so the next record in this same batch is also
            # deduplicated — avoids alert bursts within a single cycle.
            cooldown_until = alert.created_at + timedelta(seconds=DEDUPE_WINDOW_SECS)

        if created_alerts:
            await self._db.flush()
            for alert in created_alerts:
                await self._db.refresh(alert)

        return created_alerts
