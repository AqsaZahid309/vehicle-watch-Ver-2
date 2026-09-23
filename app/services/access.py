"""Tenant-scoped lookups shared by every service."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.device import Device
from app.models.user import User


async def get_org_device(db: AsyncSession, device_id: uuid.UUID, requester: User) -> Device:
    """
    Fetch a device inside the requester's organization.

    Devices in another organization raise 404, not 403. Answering "forbidden"
    would confirm to an attacker that the ID exists in another tenant.
    """
    result = await db.execute(
        select(Device).where(
            Device.id == device_id,
            Device.organization_id == requester.organization_id,
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        raise NotFoundError("Device", str(device_id))
    return device


def org_device_ids(requester: User):
    """Subquery of device IDs visible to the requester."""
    return select(Device.id).where(Device.organization_id == requester.organization_id)
