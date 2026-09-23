import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.alert import AlertSeverity
from app.models.notification import ChannelType, EventType

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class ChannelBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    channel_type: ChannelType
    target: str = Field(min_length=3, max_length=1024)
    min_severity: AlertSeverity = AlertSeverity.MEDIUM
    event_types: list[EventType] = Field(
        default_factory=lambda: [EventType.ALERT, EventType.ESCALATION, EventType.FORECAST]
    )
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_target(self) -> "ChannelBase":
        t = self.target.strip()
        if self.channel_type == ChannelType.EMAIL and not _EMAIL_RE.match(t):
            raise ValueError("EMAIL target must be an email address")
        if self.channel_type == ChannelType.SMS and not _PHONE_RE.match(t):
            raise ValueError("SMS target must be an E.164 phone number, e.g. +447700900123")
        if self.channel_type in (ChannelType.SLACK, ChannelType.TEAMS, ChannelType.WEBHOOK):
            if not t.startswith("https://") and not t.startswith("http://"):
                raise ValueError(f"{self.channel_type.value} target must be an http(s) URL")
        self.target = t
        return self


class ChannelCreate(ChannelBase):
    pass


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    target: str | None = Field(default=None, min_length=3, max_length=1024)
    min_severity: AlertSeverity | None = None
    event_types: list[EventType] | None = None
    enabled: bool | None = None


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    channel_type: ChannelType
    target: str
    min_severity: AlertSeverity
    event_types: list[EventType]
    enabled: bool
    created_at: datetime


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    severity: str | None
    title: str
    body: str
    link: str | None
    device_id: uuid.UUID | None
    alert_id: uuid.UUID | None
    read: bool
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationRead]
    unread: int


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    notification_id: uuid.UUID
    channel_id: uuid.UUID | None
    status: str
    error: str | None
    created_at: datetime
