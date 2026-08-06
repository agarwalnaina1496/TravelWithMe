"""Owned trip persistence HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..core import get_logger, get_trip_persistence
from ..persistence.contracts import TripRecord, VersionConflictError
from ..persistence.service import TripPersistenceService
from ..schemas.trips import TripCommandRequest, TripCommandResponse, TripCreateRequest, TripListResponse, TripRenameRequest, TripResponse, TripSummary, TripUiStateRequest
from ..services import AgentEngine
from ..services.trip_commands import IdempotencyConflictError, InvalidTripCommandError, TripCommandService
from ..core import get_engine
from ..telemetry import TelemetryLogger

router = APIRouter(prefix="/trips", tags=["Trips"])
Persistence = Annotated[TripPersistenceService, Depends(get_trip_persistence)]
Logger = Annotated[TelemetryLogger, Depends(get_logger)]
Engine = Annotated[AgentEngine, Depends(get_engine)]


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
    trip = await persistence.repository.create_trip(
        guest.id, payload.title, payload.product_mode, {}, {}
    )
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


@router.patch("/{trip_id}/ui-state", response_model=TripResponse)
async def update_ui_state(trip_id: UUID, payload: TripUiStateRequest, request: Request, response: Response, persistence: Persistence, logger: Logger):
    guest = await persistence.guest(request, response)
    try:
        trip = await persistence.repository.update_ui_state(
            guest.id, trip_id, payload.expected_version, payload.ui_state
        )
    except VersionConflictError as error:
        logger.warning(
            "Rejected stale trip UI-state update.",
            event="be.trip.ui_state.version_conflict",
            source="http",
            trip_id=str(trip_id),
            current_version=error.current_version,
        )
        raise _conflict(error) from error
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    logger.info(
        "Updated guest trip UI state.",
        event="be.trip.ui_state.updated",
        source="http",
        trip_id=str(trip_id),
        version=trip.version,
    )
    return _response(trip)


@router.post("/{trip_id}/commands", response_model=TripCommandResponse)
async def execute_trip_command(
    trip_id: UUID,
    payload: TripCommandRequest,
    request: Request,
    response: Response,
    persistence: Persistence,
    engine: Engine,
    logger: Logger,
):
    guest = await persistence.guest(request, response)
    trip = await persistence.repository.get_trip(guest.id, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    logger.info(
        "Received Backend-owned trip command.",
        event="be.trip.command.received",
        source="http",
        trip_id=str(trip_id),
        command=payload.command,
        expected_version=payload.expected_version,
        idempotency_key=str(payload.idempotency_key),
    )
    service = TripCommandService(persistence.repository, engine, logger)
    try:
        return await service.execute(guest.id, trip, payload)
    except VersionConflictError as error:
        logger.warning(
            "Rejected stale trip command.",
            event="be.trip.command.version_conflict",
            source="http",
            trip_id=str(trip_id),
            command=payload.command,
            current_version=error.current_version,
        )
        raise _conflict(error) from error
    except IdempotencyConflictError as error:
        logger.warning(
            "Rejected trip command because its idempotency key was reused.",
            event="be.trip.command.idempotency_conflict",
            source="http",
            trip_id=str(trip_id),
            command=payload.command,
        )
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidTripCommandError as error:
        logger.warning(
            "Rejected invalid Backend-owned trip command.",
            event="be.trip.command.invalid_transition",
            source="http",
            trip_id=str(trip_id),
            command=payload.command,
            detail=str(error),
        )
        raise HTTPException(status_code=422, detail=str(error)) from error
