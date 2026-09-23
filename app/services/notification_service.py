"""
Notifications: an in-app feed plus outbound delivery (email, Slack, Teams,
generic webhook, SMS via Twilio).

Two phases, on purpose:
  1. create()  — writes the Notification row on the caller's session and pushes
                 it to the live stream. Cheap, transactional.
  2. deliver() — sends it through every matching channel using its *own*
                 session, after the caller has committed. Network calls to
                 Slack or an SMTP server never hold a DB transaction open, and
                 a failing channel never rolls back the alert that triggered it.
"""

import asyncio
import ipaddress
import logging
import smtplib
import socket
import uuid
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import events
from app.core.metrics import NOTIFICATIONS_SENT
from app.models.notification import (
    ChannelType, Notification, NotificationChannel, NotificationDelivery,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "CRITICAL": 2}
_HTTP_TIMEOUT = 10.0


class DeliveryError(Exception):
    pass


def channel_accepts(channel: NotificationChannel, event_type: str, severity: str | None) -> bool:
    if not channel.enabled:
        return False
    if channel.event_types and event_type not in channel.event_types:
        return False
    if severity is None:
        return True
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(channel.min_severity, 0)


def assert_public_url(url: str) -> None:
    """
    SSRF guard for user-supplied webhook URLs: refuse hosts that resolve to
    loopback, private, link-local (cloud metadata) or reserved addresses.
    Enforced in production; development allows localhost webhooks for testing.
    """
    if not settings.is_production:
        return
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise DeliveryError("Webhook URLs must use https in production")
    host = parsed.hostname or ""
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DeliveryError(f"Cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise DeliveryError(f"Refusing to call non-public address {ip}")


def _link(n: Notification) -> str:
    return f"{settings.public_base_url.rstrip('/')}{n.link or '/'}"


async def _send_http(url: str, payload: dict[str, Any]) -> None:
    assert_public_url(url)
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code >= 300:
        raise DeliveryError(f"HTTP {resp.status_code}: {resp.text[:200]}")


def _send_email_sync(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise DeliveryError("SMTP is not configured (set SMTP_HOST)")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=_HTTP_TIMEOUT) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def _send_sms(to: str, text: str) -> None:
    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number):
        raise DeliveryError("Twilio is not configured (set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            url,
            data={"To": to, "From": settings.twilio_from_number, "Body": text[:1500]},
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
    if resp.status_code >= 300:
        raise DeliveryError(f"Twilio HTTP {resp.status_code}: {resp.text[:200]}")


async def send_via_channel(channel: NotificationChannel, n: Notification) -> None:
    """Deliver one notification through one channel. Raises on failure."""
    sev = f"[{n.severity}] " if n.severity else ""
    ctype = channel.channel_type
    if ctype == ChannelType.SLACK.value:
        await _send_http(channel.target, {
            "text": f"{sev}*{n.title}*\n{n.body}\n<{_link(n)}|Open in VehicleWatch>",
        })
    elif ctype == ChannelType.TEAMS.value:
        await _send_http(channel.target, {
            "text": f"{sev}**{n.title}**\n\n{n.body}\n\n[Open in VehicleWatch]({_link(n)})",
        })
    elif ctype == ChannelType.WEBHOOK.value:
        await _send_http(channel.target, {
            "id": str(n.id), "event_type": n.event_type, "severity": n.severity,
            "title": n.title, "body": n.body, "link": _link(n),
            "device_id": str(n.device_id) if n.device_id else None,
            "alert_id": str(n.alert_id) if n.alert_id else None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        })
    elif ctype == ChannelType.EMAIL.value:
        await asyncio.to_thread(
            _send_email_sync, channel.target, f"{sev}{n.title}", f"{n.body}\n\n{_link(n)}"
        )
    elif ctype == ChannelType.SMS.value:
        await _send_sms(channel.target, f"{sev}{n.title}: {n.body}")
    else:
        raise DeliveryError(f"Unsupported channel type {ctype}")


class NotificationService:
    def __init__(self, db: AsyncSession, redis: Any = None) -> None:
        self._db = db
        self._redis = redis

    async def create(
        self,
        org_id: uuid.UUID,
        event_type: str,
        title: str,
        body: str,
        *,
        severity: str | None = None,
        device_id: uuid.UUID | None = None,
        alert_id: uuid.UUID | None = None,
        link: str | None = None,
    ) -> Notification:
        n = Notification(
            organization_id=org_id, event_type=event_type, severity=severity,
            title=title[:255], body=body, link=link, device_id=device_id, alert_id=alert_id,
        )
        self._db.add(n)
        await self._db.flush()
        await events.publish(self._redis, org_id, "notification", {
            "id": str(n.id), "event_type": event_type, "severity": severity,
            "title": n.title, "body": body, "link": link,
        })
        return n

    async def has_channels(self, org_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            select(NotificationChannel.id).where(
                NotificationChannel.organization_id == org_id,
                NotificationChannel.enabled.is_(True),
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def deliver(self, notification_ids: list[uuid.UUID]) -> int:
        """Send already-committed notifications through matching channels. Returns successes."""
        if not notification_ids:
            return 0
        notifications = (
            await self._db.execute(select(Notification).where(Notification.id.in_(notification_ids)))
        ).scalars().all()
        org_ids = {n.organization_id for n in notifications}
        channels = (
            await self._db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.organization_id.in_(org_ids),
                    NotificationChannel.enabled.is_(True),
                )
            )
        ).scalars().all()

        sent = 0
        for n in notifications:
            for ch in channels:
                if ch.organization_id != n.organization_id or not channel_accepts(ch, n.event_type, n.severity):
                    continue
                try:
                    await send_via_channel(ch, n)
                    status, error = "SENT", None
                    sent += 1
                except Exception as exc:
                    status, error = "FAILED", str(exc)[:1000]
                    logger.warning("Notification %s via %s (%s) failed: %s", n.id, ch.name, ch.channel_type, exc)
                NOTIFICATIONS_SENT.labels(ch.channel_type, status).inc()
                self._db.add(NotificationDelivery(notification_id=n.id, channel_id=ch.id,
                                                  status=status, error=error))
        await self._db.commit()
        return sent


async def deliver_in_new_session(notification_ids: list[uuid.UUID]) -> None:
    """Entry point for FastAPI BackgroundTasks and the worker."""
    if not notification_ids:
        return
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await NotificationService(db).deliver(notification_ids)
    except Exception as exc:  # delivery is best-effort; never crash the caller
        logger.exception("Notification delivery failed: %s", exc)
