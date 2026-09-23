import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.maintenance import WorkOrderPriority, WorkOrderStatus

RootCause = Literal[
    "COOLANT_LEAK", "BATTERY_FAILURE", "TRANSMISSION_STRESS", "BRAKE_WEAR",
    "ENGINE_STRESS", "WHEEL_BEARING", "LOW_OIL_PRESSURE", "TIRE_PRESSURE",
    "OTHER_FAULT", "PREVENTIVE_SERVICE", "NO_FAULT_FOUND",
]


class WorkOrderCreate(BaseModel):
    device_id: uuid.UUID | None = None  # inferred from alert_id when omitted
    alert_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    assigned_to_id: uuid.UUID | None = None
    due_date: datetime | None = None

    @model_validator(mode="after")
    def _need_target(self) -> "WorkOrderCreate":
        if not self.device_id and not self.alert_id:
            raise ValueError("Either device_id or alert_id is required")
        return self


class WorkOrderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    status: WorkOrderStatus | None = None
    priority: WorkOrderPriority | None = None
    assigned_to_id: uuid.UUID | None = None
    unassign: bool = False
    root_cause: RootCause | None = None
    resolution_notes: str | None = Field(default=None, max_length=10000)
    parts_cost: float | None = Field(default=None, ge=0)
    labor_cost: float | None = Field(default=None, ge=0)
    downtime_hours: float | None = Field(default=None, ge=0)
    due_date: datetime | None = None


class WorkOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    device_id: uuid.UUID
    device_name: str | None = None
    alert_id: uuid.UUID | None
    schedule_id: uuid.UUID | None
    title: str
    description: str | None
    status: WorkOrderStatus
    priority: WorkOrderPriority
    assigned_to_id: uuid.UUID | None
    assigned_to_email: str | None = None
    created_by_id: uuid.UUID | None
    root_cause: str | None
    resolution_notes: str | None
    parts_cost: float
    labor_cost: float
    downtime_hours: float
    total_cost: float = 0.0
    due_date: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    resolved_at: datetime | None


class PaginatedWorkOrders(BaseModel):
    items: list[WorkOrderRead]
    total: int
    page: int
    page_size: int
    pages: int


class ServiceScheduleCreate(BaseModel):
    device_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    interval_km: float | None = Field(default=None, gt=0)
    interval_days: int | None = Field(default=None, gt=0, le=3650)
    last_service_at: datetime | None = None
    last_service_km: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _need_interval(self) -> "ServiceScheduleCreate":
        if not self.interval_km and not self.interval_days:
            raise ValueError("Set interval_km, interval_days, or both")
        return self


class ServiceScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    interval_km: float | None = Field(default=None, gt=0)
    interval_days: int | None = Field(default=None, gt=0, le=3650)
    is_active: bool | None = None


class ServiceScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str | None = None
    name: str
    interval_km: float | None
    interval_days: int | None
    last_service_at: datetime
    last_service_km: float
    is_active: bool
    # Computed
    km_remaining: float | None = None
    days_remaining: float | None = None
    due: bool = False
