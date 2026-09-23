import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_manager
from app.models.user import User
from app.redis import get_redis_binary_pool
from app.schemas.ml import DetectorMetrics, ModelVersionRead
from app.services.ml_service import MLService

router = APIRouter(prefix="/ml", tags=["ML models"])


def _binary_redis():
    try:
        return get_redis_binary_pool()
    except RuntimeError:
        return None


@router.get("/models", response_model=list[ModelVersionRead])
async def list_models(
    device_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: User = Depends(get_current_user),
):
    """Model registry: every trained version with its window, feature statistics and drift score."""
    return await MLService(db).list_versions(user, device_id)


@router.get("/active")
async def active_models(db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return await MLService(db).active_models(user)


@router.post("/models/{version_id}/pin", response_model=ModelVersionRead)
async def pin_model(version_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(require_manager)):
    """Lock this version in as the known-good baseline — automatic retraining will not replace it."""
    return await MLService(db).pin(version_id, user, True)


@router.post("/models/{version_id}/unpin", response_model=ModelVersionRead)
async def unpin_model(version_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(require_manager)):
    return await MLService(db).pin(version_id, user, False)


@router.post("/devices/{device_id}/retrain", status_code=status.HTTP_202_ACCEPTED)
async def retrain(device_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(require_manager)):
    """Queue a retrain; it runs on the next worker cycle (overrides a pin once)."""
    await MLService(db, _binary_redis()).retrain(device_id, user)
    return {"status": "queued"}


@router.get("/metrics", response_model=DetectorMetrics)
async def detector_metrics(db: AsyncSession = Depends(get_db, scope="function"), user: User = Depends(get_current_user)):
    """Precision per fault type and per vehicle, from operator feedback and resolved work orders."""
    return await MLService(db).detector_metrics(user)
