import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.alert import AlertFeedback, AlertSeverity, FaultConfidence, FaultType


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str | None = None
    telemetry_id: uuid.UUID | None
    severity: AlertSeverity
    anomaly_score: float
    affected_metrics: dict
    fault_type: FaultType | None
    fault_confidence: FaultConfidence | None
    llm_summary: str | None
    acknowledged: bool
    acknowledged_at: datetime | None = None
    acknowledged_by_id: uuid.UUID | None = None
    feedback: AlertFeedback | None = None
    feedback_notes: str | None = None
    escalated_at: datetime | None = None
    work_order_id: uuid.UUID | None = None
    created_at: datetime


class AlertDetail(AlertRead):
    telemetry: dict | None = None


class AlertAcknowledge(BaseModel):
    acknowledged: bool = True


class AlertFeedbackIn(BaseModel):
    feedback: AlertFeedback
    notes: str | None = Field(default=None, max_length=2000)


class BulkAcknowledge(BaseModel):
    alert_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class PaginatedAlerts(BaseModel):
    items: list[AlertRead]
    total: int
    page: int
    page_size: int
    pages: int
