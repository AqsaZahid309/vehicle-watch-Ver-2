"""Model registry operations and detector quality metrics."""

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.alert import Alert, AlertFeedback
from app.models.device import Device
from app.models.ml import ModelVersion
from app.models.user import User
from app.schemas.ml import DetectorMetrics, FaultPrecision, ModelVersionRead
from app.services.access import get_org_device, org_device_ids
from app.services.anomaly_service import request_retrain
from app.services.audit_service import record_audit


class MLService:
    def __init__(self, db: AsyncSession, redis: Any = None) -> None:
        self._db = db
        self._redis = redis

    async def _read(self, versions: list[ModelVersion]) -> list[ModelVersionRead]:
        ids = [v.id for v in versions]
        with_artifact = set()
        if ids:
            with_artifact = set(
                (
                    await self._db.execute(
                        select(ModelVersion.id).where(ModelVersion.id.in_(ids), ModelVersion.artifact.is_not(None))
                    )
                ).scalars().all()
            )
        out = []
        for v in versions:
            r = ModelVersionRead.model_validate(v)
            r.has_artifact = v.id in with_artifact
            out.append(r)
        return out

    async def list_versions(self, requester: User, device_id: uuid.UUID | None = None) -> list[ModelVersionRead]:
        stmt = select(ModelVersion).where(ModelVersion.organization_id == requester.organization_id)
        if device_id:
            device = await get_org_device(self._db, device_id, requester)
            stmt = stmt.where(
                (ModelVersion.device_id == device_id)
                | ((ModelVersion.scope == "CLASS") & (ModelVersion.device_type == device.device_type))
            )
        rows = (await self._db.execute(stmt.order_by(ModelVersion.trained_at.desc()).limit(200))).scalars().all()
        return await self._read(list(rows))

    async def active_models(self, requester: User) -> list[dict[str, Any]]:
        """One row per device: which model is scoring it right now."""
        devices = (
            await self._db.execute(select(Device).where(Device.organization_id == requester.organization_id))
        ).scalars().unique().all()
        active = (
            await self._db.execute(
                select(ModelVersion).where(
                    ModelVersion.organization_id == requester.organization_id, ModelVersion.is_active.is_(True)
                )
            )
        ).scalars().all()
        by_device = {v.device_id: v for v in active if v.scope == "DEVICE"}
        by_class = {v.device_type: v for v in active if v.scope == "CLASS"}
        out = []
        for d in devices:
            v = by_device.get(d.id) or by_class.get(d.device_type)
            out.append({
                "device_id": str(d.id), "device_name": d.name, "device_type": d.device_type,
                "model": (await self._read([v]))[0].model_dump(mode="json") if v else None,
            })
        return out

    async def _version(self, version_id: uuid.UUID, requester: User) -> ModelVersion:
        v = (
            await self._db.execute(
                select(ModelVersion).where(
                    ModelVersion.id == version_id, ModelVersion.organization_id == requester.organization_id
                )
            )
        ).scalar_one_or_none()
        if not v:
            raise NotFoundError("Model version", str(version_id))
        return v

    async def pin(self, version_id: uuid.UUID, requester: User, pinned: bool) -> ModelVersionRead:
        """
        Pinning makes this version the active model for its device (or class) and
        exempts it from automatic retraining. Only versions whose artifact is still
        stored can be pinned.
        """
        v = await self._version(version_id, requester)
        if pinned:
            has_artifact = (
                await self._db.execute(
                    select(ModelVersion.id).where(ModelVersion.id == v.id, ModelVersion.artifact.is_not(None))
                )
            ).scalar_one_or_none()
            if not has_artifact:
                raise NotFoundError("Model artifact (it was pruned — pin a newer version)")
            same_scope = [
                ModelVersion.organization_id == v.organization_id,
                ModelVersion.scope == v.scope,
                (ModelVersion.device_id == v.device_id) if v.scope == "DEVICE"
                else (ModelVersion.device_type == v.device_type),
            ]
            await self._db.execute(
                update(ModelVersion).where(*same_scope, ModelVersion.id != v.id)
                .values(is_active=False, pinned=False)
                .execution_options(synchronize_session=False)
            )
            v.is_active = True
        v.pinned = pinned
        record_audit(self._db, requester, "model.pinned" if pinned else "model.unpinned", "model_version", v.id,
                     {"version": v.version, "device_id": str(v.device_id) if v.device_id else None})
        await self._db.flush()
        await self._db.refresh(v)
        return (await self._read([v]))[0]

    async def retrain(self, device_id: uuid.UUID, requester: User) -> None:
        await get_org_device(self._db, device_id, requester)
        await request_retrain(self._redis, device_id)
        record_audit(self._db, requester, "model.retrain_requested", "device", device_id)

    async def detector_metrics(self, requester: User) -> DetectorMetrics:
        rows = (
            await self._db.execute(
                select(Alert.fault_type, Alert.feedback, Alert.device_id)
                .where(Alert.device_id.in_(org_device_ids(requester)))
            )
        ).all()
        names = {
            d.id: d.name
            for d in (
                await self._db.execute(select(Device).where(Device.organization_id == requester.organization_id))
            ).scalars().unique().all()
        }

        def blank() -> dict[str, int]:
            return {"alerts": 0, "tp": 0, "fp": 0}

        by_fault: dict[str, dict[str, int]] = defaultdict(blank)
        by_device: dict[uuid.UUID, dict[str, int]] = defaultdict(blank)
        for fault_type, feedback, device_id in rows:
            key = fault_type.value if fault_type else "UNCLASSIFIED"
            for bucket in (by_fault[key], by_device[device_id]):
                bucket["alerts"] += 1
                if feedback == AlertFeedback.TRUE_POSITIVE.value:
                    bucket["tp"] += 1
                elif feedback == AlertFeedback.FALSE_POSITIVE.value:
                    bucket["fp"] += 1

        def precision(b: dict[str, int]) -> float | None:
            labelled = b["tp"] + b["fp"]
            return round(b["tp"] / labelled, 3) if labelled else None

        tp = sum(b["tp"] for b in by_fault.values())
        fp = sum(b["fp"] for b in by_fault.values())
        return DetectorMetrics(
            total_alerts=len(rows),
            labelled_alerts=tp + fp,
            precision=round(tp / (tp + fp), 3) if tp + fp else None,
            by_fault_type=sorted(
                [
                    FaultPrecision(fault_type=k, alerts=b["alerts"], labelled=b["tp"] + b["fp"],
                                   true_positive=b["tp"], false_positive=b["fp"], precision=precision(b))
                    for k, b in by_fault.items()
                ],
                key=lambda f: -f.alerts,
            ),
            by_device=sorted(
                [
                    {"device_id": str(k), "device_name": names.get(k, "?"), "alerts": b["alerts"],
                     "true_positive": b["tp"], "false_positive": b["fp"], "precision": precision(b)}
                    for k, b in by_device.items()
                ],
                key=lambda d: -d["alerts"],
            ),
        )
