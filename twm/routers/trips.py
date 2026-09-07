"""Owned trip persistence HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..dependencies import get_current_user, get_engine, get_logger, get_trip_persistence, get_trip_view_service
from ..persistence.contracts import TripOwner, TripRecord, User, VersionConflictError
from ..persistence.service import TripPersistenceService
from ..schemas.trips import (
    TripCommandRequest,
    TripCommandResponse,
    TripCreateRequest,
    TripFirstMessageRequest,
    TripItineraryResponse,
    TripRecommendationsResponse,
    TripRenameRequest,
    TripResponse,
    TripUiStateRequest,
)
from ..schemas.trip_view import TripListItem, TripListResponse, TripView
from ..services import AgentEngine
from ..services.trip_commands import IdempotencyConflictError, InvalidTripCommandError, TripCommandService
from ..services.trip_view import TripViewService, compose_trip_dates, enrich_itinerary
from ..telemetry import TelemetryLogger

router = APIRouter(prefix="/trips", tags=["Trips"])
Persistence = Annotated[TripPersistenceService, Depends(get_trip_persistence)]
Logger = Annotated[TelemetryLogger, Depends(get_logger)]
Engine = Annotated[AgentEngine, Depends(get_engine)]
CurrentUser = Annotated[User | None, Depends(get_current_user)]
TripViewDep = Annotated[TripViewService, Depends(get_trip_view_service)]


async def _resolve_owner(request: Request, response: Response, persistence: TripPersistenceService, current_user: User | None) -> TripOwner:
    """Every request still carries/creates a guest session (TWM-64); an
    authenticated request additionally resolves by user_id, which takes
    precedence — a claimed trip is no longer reachable via its originating
    guest cookie (TWM-179)."""
    guest = await persistence.guest(request, response)
    return TripOwner(guest_session_id=guest.id, user_id=current_user.id if current_user else None)


async def _compose_trip_view(
    owner: TripOwner,
    trip_id: UUID,
    persistence: TripPersistenceService,
    view_service: TripViewService,
) -> TripView | None:
    """GET / PATCH /trips/{id} (TWM-217): the one composed read model. Uses
    the blob-free get_trip_core; reads itinerary_versions once, and only when
    an itinerary pointer exists, to compose summary / budget_breakdown /
    before_you_go."""
    trip = await persistence.repository.get_trip_core(owner, trip_id)
    if trip is None:
        return None
    pointer = (trip.trip_state.get("itinerary_state") or {}).get("current_version")
    itinerary_result = None
    if pointer:
        current = await persistence.repository.get_current_itinerary(owner, trip_id)
        itinerary_result = current.result if current else None
    has_recommendation = bool(
        await persistence.repository.trip_ids_with_recommendations(owner, [trip_id])
    )
    return view_service.build(
        trip_id=trip_id,
        title=trip.title,
        product_mode=trip.product_mode,
        version=trip.version,
        trip_state=trip.trip_state,
        ui_state=trip.ui_state,
        itinerary_result=itinerary_result,
        has_recommendation=has_recommendation,
    )


def _has_trip_context(record: TripRecord) -> bool:
    """TWM-188: a trip with no trip_context yet only exists because
    creation (POST /trips) is lazy but not atomic with the first real
    command — if that first command never lands (network failure, an
    abandoned tab), the record is an orphan, not a real trip. My Trips/
    Landing should never see it."""
    return bool(record.trip_state.get("trip_context"))


@router.get("", response_model=TripListResponse)
async def list_trips(
    request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser,
    trip_view: TripViewDep,
    limit: int = Query(default=200, ge=1),
):
    # TWM-191: `limit` is a generous safety bound (a value above the
    # repository max is clamped, never rejected) — the UI shows every trip
    # and has no "load more" yet, so there is no cursor.
    owner = await _resolve_owner(request, response, persistence, current_user)
    trips = await persistence.repository.list_trips(owner, limit)
    populated_trips = [t for t in trips if _has_trip_context(t)]
    recommendation_ids = await persistence.repository.trip_ids_with_recommendations(
        owner, [t.id for t in populated_trips]
    )
    logger.info(
        "Listed guest trips.",
        event="be.trip.listed",
        source="http",
        guest_id=str(owner.guest_session_id),
        authenticated=owner.is_authenticated,
        count=len(populated_trips),
        empty_excluded=len(trips) - len(populated_trips),
    )
    return TripListResponse(trips=[
        trip_view.build_list_item(
            trip_id=t.id, title=t.title, product_mode=t.product_mode, version=t.version,
            created_at=t.created_at, updated_at=t.updated_at, trip_state=t.trip_state,
            has_recommendation=t.id in recommendation_ids,
        )
        for t in populated_trips
    ])


@router.post("", response_model=TripView, status_code=201)
async def create_trip(
    payload: TripCreateRequest, request: Request, response: Response,
    persistence: Persistence, logger: Logger, current_user: CurrentUser, trip_view: TripViewDep,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.create_trip(
        owner.guest_session_id, owner.user_id, payload.title, payload.product_mode, {"trip_context": payload.trip_context}, {}
    )
    logger.info("Created guest trip.", event="be.trip.created", source="http", trip_id=str(trip.id), version=trip.version)
    return trip_view.build(
        trip_id=trip.id, title=trip.title, product_mode=trip.product_mode, version=trip.version,
        trip_state=trip.trip_state, ui_state=trip.ui_state, itinerary_result=None, has_recommendation=False,
    )


@router.post("/first-message", response_model=TripCommandResponse, status_code=201)
async def start_trip_from_first_message(
    payload: TripFirstMessageRequest,
    request: Request,
    response: Response,
    persistence: Persistence,
    engine: Engine,
    logger: Logger,
    current_user: CurrentUser,
):
    """TWM-189: the only path that creates a trip on the traveler's first
    message — runs the agent turn before any row exists, and only persists
    a row if that turn succeeds, so a failure never leaves an orphan trip.
    """
    owner = await _resolve_owner(request, response, persistence, current_user)
    logger.info(
        "Received first-message trip start.",
        event="be.trip.first_message.received",
        source="http",
        entry_intent=payload.entry_intent,
    )
    service = TripCommandService(persistence.repository, engine, logger)
    try:
        return await service.execute_first_message(owner, payload)
    except InvalidTripCommandError as error:
        logger.warning(
            "Rejected invalid first-message trip start.",
            event="be.trip.first_message.invalid",
            source="http",
            entry_intent=payload.entry_intent,
            detail=str(error),
        )
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{trip_id}", response_model=TripView)
async def get_trip(
    trip_id: UUID, request: Request, response: Response,
    persistence: Persistence, logger: Logger, current_user: CurrentUser, trip_view: TripViewDep,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    view = await _compose_trip_view(owner, trip_id, persistence, trip_view)
    if view is None:
        logger.warning("Trip not found for guest.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    logger.info(
        "Fetched guest trip.", event="be.trip.fetched", source="http",
        trip_id=str(trip_id), version=view.version, stage=view.lifecycle.stage,
        has_summary=view.summary is not None,
    )
    return view


@router.get("/{trip_id}/recommendations", response_model=TripRecommendationsResponse)
async def get_latest_recommendations(trip_id: UUID, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip_core(owner, trip_id)
    if trip is None:
        logger.warning("Trip not found for guest.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    latest = await persistence.repository.get_latest_recommendation(owner, trip_id)
    if latest is None:
        logger.info(
            "No matcher recommendations yet for trip.",
            event="be.trip.recommendations.fetched",
            source="http",
            trip_id=str(trip_id),
            found=False,
        )
        raise HTTPException(status_code=404, detail="No recommendations yet.")
    logger.info(
        "Fetched latest matcher recommendations.",
        event="be.trip.recommendations.fetched",
        source="http",
        trip_id=str(trip_id),
        found=True,
        version=latest.version,
    )
    return TripRecommendationsResponse.model_validate(latest, from_attributes=True)


@router.get("/{trip_id}/itinerary", response_model=TripItineraryResponse)
async def get_current_itinerary(trip_id: UUID, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    """TWM-217: the enriched Atlas document — every timeline item gains a
    stable `id`, `is_gateway_leg`, and a resolved date (`resolved_date` /
    `date_precision` / `date_source`); top-level `stay_segments[]`. The
    identity / date / stay-grouping logic is the former TripBoardService,
    relocated. No `feasible_modes` — that is a per-leg feasibility call."""
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip_core(owner, trip_id)
    if trip is None:
        logger.warning("Trip not found for guest.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    current = await persistence.repository.get_current_itinerary(owner, trip_id)
    if current is None:
        logger.info(
            "No active itinerary yet for trip.",
            event="be.trip.itinerary.fetched",
            source="http",
            trip_id=str(trip_id),
            found=False,
        )
        raise HTTPException(status_code=404, detail="No itinerary yet.")
    final_itinerary = current.result["final_itinerary"]
    trip_dates = compose_trip_dates(
        trip.trip_state.get("trip_context") or {}, len(final_itinerary.get("days") or [])
    )
    enriched = enrich_itinerary(
        trip_id,
        final_itinerary,
        trip.trip_state.get("trip_context") or {},
        trip.trip_state.get("booking_setup") or {},
        trip_dates,
    )
    stay_segments = enriched.pop("stay_segments")
    logger.info(
        "Fetched active itinerary.",
        event="be.trip.itinerary.fetched",
        source="http",
        trip_id=str(trip_id),
        found=True,
        version=current.version,
        enriched=True,
    )
    return TripItineraryResponse(
        version=current.version,
        source_guide_revision=current.source_guide_revision,
        result={**current.result, "final_itinerary": enriched, "stay_segments": stay_segments},
        created_at=current.created_at,
    )


def _conflict(error: VersionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": "Trip has a newer version.", "current_version": error.current_version})


@router.patch("/{trip_id}", response_model=TripView)
async def rename_trip(
    trip_id: UUID, payload: TripRenameRequest, request: Request, response: Response,
    persistence: Persistence, logger: Logger, current_user: CurrentUser, trip_view: TripViewDep,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    try:
        trip = await persistence.repository.rename_trip(owner, trip_id, payload.expected_version, payload.title)
    except VersionConflictError as error:
        logger.warning("Rejected stale trip rename.", event="be.trip.version_conflict", source="http", trip_id=str(trip_id), current_version=error.current_version)
        raise _conflict(error) from error
    if trip is None:
        logger.warning("Trip not found for guest rename.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    logger.info("Renamed guest trip.", event="be.trip.renamed", source="http", trip_id=str(trip_id), version=trip.version)
    return await _compose_trip_view(owner, trip_id, persistence, trip_view)


@router.patch("/{trip_id}/ui-state", response_model=TripView)
async def update_ui_state(
    trip_id: UUID, payload: TripUiStateRequest, request: Request, response: Response,
    persistence: Persistence, logger: Logger, current_user: CurrentUser, trip_view: TripViewDep,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    try:
        trip = await persistence.repository.update_ui_state(
            owner, trip_id, payload.expected_version, payload.ui_state
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
    return await _compose_trip_view(owner, trip_id, persistence, trip_view)


@router.post("/{trip_id}/commands", response_model=TripCommandResponse)
async def execute_trip_command(
    trip_id: UUID,
    payload: TripCommandRequest,
    request: Request,
    response: Response,
    persistence: Persistence,
    engine: Engine,
    logger: Logger,
    current_user: CurrentUser,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip_core(owner, trip_id)
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
        return await service.execute(owner, trip, payload)
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
