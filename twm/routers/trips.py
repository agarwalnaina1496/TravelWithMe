"""Owned trip persistence HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..core import get_logger, get_trip_persistence
from ..persistence.contracts import TripRecord, VersionConflictError
from ..persistence.service import TripPersistenceService
from ..schemas.trips import TripCreateRequest, TripListResponse, TripRenameRequest, TripReplaceRequest, TripResponse, TripSummary
from ..telemetry import TelemetryLogger

router = APIRouter(prefix="/trips", tags=["Trips"])
Persistence = Annotated[TripPersistenceService, Depends(get_trip_persistence)]
Logger = Annotated[TelemetryLogger, Depends(get_logger)]


def _response(record: TripRecord) -> TripResponse:
    return TripResponse.model_validate(record, from_attributes=True)


@router.get("", response_model=TripListResponse)
async def list_trips(request: Request, response: Response, persistence: Persistence):
    guest = await persistence.guest(request, response)
    trips = await persistence.repository.list_trips(guest.id)
    return TripListResponse(trips=[TripSummary.model_validate(t, from_attributes=True) for t in trips])


@router.post("", response_model=TripResponse, status_code=201)
async def create_trip(payload: TripCreateRequest, request: Request, response: Response, persistence: Persistence, logger: Logger):
    guest = await persistence.guest(request, response)
    trip = await persistence.repository.create_trip(guest.id, payload.title, payload.product_mode, payload.trip_state, payload.ui_state)
    logger.info("Created guest trip.", event="be.trip.created", source="http", trip_id=str(trip.id), version=trip.version)
    return _response(trip)


@router.get("/{trip_id}", response_model=TripResponse)
async def get_trip(trip_id: UUID, request: Request, response: Response, persistence: Persistence):
    guest = await persistence.guest(request, response)
    trip = await persistence.repository.get_trip(guest.id, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return _response(trip)


def _conflict(error: VersionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": "Trip has a newer version.", "current_version": error.current_version})


@router.put("/{trip_id}", response_model=TripResponse)
async def replace_trip(trip_id: UUID, payload: TripReplaceRequest, request: Request, response: Response, persistence: Persistence, logger: Logger):
    guest = await persistence.guest(request, response)
    try:
        trip = await persistence.repository.replace_trip(guest.id, trip_id, payload.expected_version, payload.trip_state, payload.ui_state)
    except VersionConflictError as error:
        logger.warning("Rejected stale trip replacement.", event="be.trip.version_conflict", source="http", trip_id=str(trip_id), current_version=error.current_version)
        raise _conflict(error) from error
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return _response(trip)


@router.patch("/{trip_id}", response_model=TripResponse)
async def rename_trip(trip_id: UUID, payload: TripRenameRequest, request: Request, response: Response, persistence: Persistence, logger: Logger):
    guest = await persistence.guest(request, response)
    try:
        trip = await persistence.repository.rename_trip(guest.id, trip_id, payload.expected_version, payload.title)
    except VersionConflictError as error:
        logger.warning("Rejected stale trip rename.", event="be.trip.version_conflict", source="http", trip_id=str(trip_id), current_version=error.current_version)
        raise _conflict(error) from error
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return _response(trip)
