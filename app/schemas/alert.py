import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.alert import AlertSeverity, FaultConfidence, FaultType


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    telemetry_id: uuid.UUID | None
    severity: AlertSeverity
    anomaly_score: float
    affected_metrics: dict
    fault_type: FaultType | None
    fault_confidence: FaultConfidence | None
    llm_summary: str | None
    acknowledged: bool
    created_at: datetime


class AlertAcknowledge(BaseModel):
    acknowledged: bool = True


class PaginatedAlerts(BaseModel):
    items: list[AlertRead]
    total: int
    page: int
    page_size: int
    pages: int
