import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: str
    device_id: uuid.UUID | None
    device_type: str
    version: int
    reason: str
    n_train: int
    window_start: datetime | None
    window_end: datetime | None
    feature_stats: dict
    drift_psi: float | None
    is_active: bool
    pinned: bool
    trained_at: datetime
    has_artifact: bool = False


class FaultPrecision(BaseModel):
    fault_type: str
    alerts: int
    labelled: int
    true_positive: int
    false_positive: int
    precision: float | None


class DetectorMetrics(BaseModel):
    total_alerts: int
    labelled_alerts: int
    precision: float | None
    by_fault_type: list[FaultPrecision]
    by_device: list[dict]


class ForecastSignal(BaseModel):
    signal: str
    label: str
    unit: str
    current: float
    threshold: float
    direction: str  # "rising" | "falling"
    slope_per_hour: float
    r2: float
    hours_to_threshold: float | None
    risk: str  # NONE | LOW | MEDIUM | HIGH | CRITICAL
    predicted_fault: str
    history: list[dict]


class DeviceForecast(BaseModel):
    device_id: uuid.UUID
    device_name: str
    sample_count: int
    window_hours: float
    overall_risk: str
    min_hours_to_failure: float | None
    signals: list[ForecastSignal]
