import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_manager, require_operator
from app.models.user import User
from app.schemas.fleet import (
    DriverCreate, DriverRead, DriverScore, DriverUpdate, FuelEventRead, FuelVehicleStat,
    GeofenceCreate, GeofenceEventRead, GeofenceRead, GeofenceUpdate, PaginatedTrips, TripPoint, TripRead,
)
from app.services.fleet_service import FleetService

router = APIRouter(tags=["Fleet operations"])


# ── Drivers ───────────────────────────────────────────────────────────────────

@router.get("/drivers", response_model=list[DriverRead])
async def list_drivers(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return [DriverRead.model_validate(d) for d in await FleetService(db).list_drivers(user)]


@router.post("/drivers", response_model=DriverRead, status_code=status.HTTP_201_CREATED)
async def create_driver(data: DriverCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    return DriverRead.model_validate(await FleetService(db).create_driver(data, user))


@router.get("/drivers/scores", response_model=list[DriverScore])
async def driver_scores(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Driver scorecards: harsh events, speeding, idling, over-revving and fuel economy."""
    return await FleetService(db).driver_scores(user, days)


@router.patch("/drivers/{driver_id}", response_model=DriverRead)
async def update_driver(
    driver_id: uuid.UUID, data: DriverUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    return DriverRead.model_validate(await FleetService(db).update_driver(driver_id, data, user))


@router.delete("/drivers/{driver_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_driver(driver_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    await FleetService(db).delete_driver(driver_id, user)


# ── Trips ─────────────────────────────────────────────────────────────────────

@router.get("/trips", response_model=PaginatedTrips)
async def list_trips(
    device_id: uuid.UUID | None = Query(default=None),
    driver_id: uuid.UUID | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await FleetService(db).list_trips(user, device_id, driver_id, start, end, page, page_size)


@router.get("/trips/{trip_id}", response_model=TripRead)
async def get_trip(trip_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await FleetService(db).get_trip(trip_id, user)


@router.get("/trips/{trip_id}/route", response_model=list[TripPoint])
async def trip_route(trip_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await FleetService(db).trip_route(trip_id, user)


# ── Geofences ─────────────────────────────────────────────────────────────────

@router.get("/geofences", response_model=list[GeofenceRead])
async def list_geofences(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await FleetService(db).list_geofences(user)


@router.post("/geofences", response_model=GeofenceRead, status_code=status.HTTP_201_CREATED)
async def create_geofence(data: GeofenceCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    return GeofenceRead.model_validate(await FleetService(db).create_geofence(data, user))


@router.get("/geofences/events", response_model=list[GeofenceEventRead])
async def geofence_events(
    geofence_id: uuid.UUID | None = Query(default=None),
    device_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await FleetService(db).geofence_events(user, geofence_id, device_id, limit)


@router.patch("/geofences/{geofence_id}", response_model=GeofenceRead)
async def update_geofence(
    geofence_id: uuid.UUID, data: GeofenceUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    return GeofenceRead.model_validate(await FleetService(db).update_geofence(geofence_id, data, user))


@router.delete("/geofences/{geofence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_geofence(geofence_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    await FleetService(db).delete_geofence(geofence_id, user)


# ── Fuel ──────────────────────────────────────────────────────────────────────

@router.get("/fuel/stats", response_model=list[FuelVehicleStat])
async def fuel_stats(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-vehicle consumption, L/100 km, cost per km, refuels and suspected thefts."""
    return await FleetService(db).fuel_stats(user, days)


@router.get("/fuel/events", response_model=list[FuelEventRead])
async def fuel_events(
    device_id: uuid.UUID | None = Query(default=None),
    event: str | None = Query(default=None, pattern="^(REFUEL|THEFT_SUSPECTED)$"),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await FleetService(db).fuel_events(user, device_id, event, limit)


@router.post("/fuel/events/{event_id}/review", status_code=status.HTTP_204_NO_CONTENT)
async def review_fuel_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_operator)):
    await FleetService(db).mark_fuel_event_reviewed(event_id, user)
