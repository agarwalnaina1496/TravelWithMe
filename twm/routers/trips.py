"""Owned trip persistence HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..dependencies import get_current_user, get_engine, get_logger, get_trip_board_service, get_trip_persistence
from ..persistence.contracts import TripOwner, TripRecord, User, VersionConflictError
from ..persistence.service import TripPersistenceService
from ..schemas.trips import (
    SUMMARY_TRIP_CONTEXT_FIELDS,
    ItineraryVersionDaySummary,
    TripCommandRequest,
    TripCommandResponse,
    TripCreateRequest,
    TripFirstMessageRequest,
    TripItineraryResponse,
    TripItineraryVersionSummary,
    TripItineraryVersionsResponse,
    TripListResponse,
    TripRecommendationsResponse,
    TripRenameRequest,
    TripResponse,
    TripSummary,
    TripSummaryItineraryState,
    TripSummaryState,
    TripUiStateRequest,
)
from ..schemas.trip_board import TripBoardResponse
from ..services import AgentEngine
from ..services.trip_board import TripBoardService
from ..services.trip_commands import IdempotencyConflictError, InvalidTripCommandError, TripCommandService
from ..telemetry import TelemetryLogger

router = APIRouter(prefix="/trips", tags=["Trips"])
Persistence = Annotated[TripPersistenceService, Depends(get_trip_persistence)]
Logger = Annotated[TelemetryLogger, Depends(get_logger)]
Engine = Annotated[AgentEngine, Depends(get_engine)]
CurrentUser = Annotated[User | None, Depends(get_current_user)]
TripBoard = Annotated[TripBoardService, Depends(get_trip_board_service)]


async def _resolve_owner(request: Request, response: Response, persistence: TripPersistenceService, current_user: User | None) -> TripOwner:
    """Every request still carries/creates a guest session (TWM-64); an
    authenticated request additionally resolves by user_id, which takes
    precedence — a claimed trip is no longer reachable via its originating
    guest cookie (TWM-179)."""
    guest = await persistence.guest(request, response)
    return TripOwner(guest_session_id=guest.id, user_id=current_user.id if current_user else None)


def _response(record: TripRecord) -> TripResponse:
    """GET /trips/{id} (TWM-159): matcher/planner/booking_setup stay inline
    (small, one shared resume call every screen relies on) — only the
    Atlas itinerary result is dropped, since only the Trip Dashboard
    screen reads it (via the dedicated /itinerary endpoint instead)."""
    trip_state = record.trip_state
    itinerary = trip_state.get("itinerary_state")
    current_version = itinerary.get("current_version") if isinstance(itinerary, dict) else None
    if isinstance(current_version, dict) and "result" in current_version:
        trip_state = {
            **trip_state,
            "itinerary_state": {
                **itinerary,
                "current_version": {key: value for key, value in current_version.items() if key != "result"},
            },
        }
    return TripResponse(
        id=record.id, title=record.title, product_mode=record.product_mode,
        trip_state=trip_state, ui_state=record.ui_state, version=record.version,
        created_at=record.created_at, updated_at=record.updated_at,
    )


def _summary(record: TripRecord, has_recommendation: bool) -> TripSummary:
    """GET /trips (TWM-159, extended TWM-182, TWM-190): a small My Trips/Landing
    recap, not the full trip_state — the list screen never reads matcher/
    logistics state or the itinerary result, so none of it belongs on a
    list card. planner_state contributes only a cheap derived
    awaiting/has_day_plan/has_places signal (never the nested day_plan/
    frozen_plan/history) — enough for the traveler-facing card to tell
    "mid-conversation" from "draft ready" without a second fetch.
    has_recommendation is looked up separately (matcher_recommendations
    lives in its own table, never embedded in trip_state) — see list_trips's
    batched trip_ids_with_recommendations call."""
    trip_state = record.trip_state
    trip_context = trip_state.get("trip_context") or {}
    recap = {key: trip_context[key] for key in SUMMARY_TRIP_CONTEXT_FIELDS if key in trip_context}
    itinerary_status = (trip_state.get("itinerary_state") or {}).get("status")
    planner_state = trip_state.get("planner_state") or {}
    conversation_context = planner_state.get("conversation_context") or {}
    return TripSummary(
        id=record.id, title=record.title, product_mode=record.product_mode,
        trip_state=TripSummaryState(
            stage=trip_state.get("stage", "new"),
            itinerary_state=TripSummaryItineraryState(status=itinerary_status),
            trip_context=recap,
            awaiting=conversation_context.get("awaiting"),
            has_day_plan=bool(planner_state.get("day_plan")),
            has_places=bool(planner_state.get("places")),
            has_recommendation=has_recommendation,
        ),
        version=record.version, created_at=record.created_at, updated_at=record.updated_at,
    )


def _has_trip_context(record: TripRecord) -> bool:
    """TWM-188: a trip with no trip_context yet only exists because
    creation (POST /trips) is lazy but not atomic with the first real
    command — if that first command never lands (network failure, an
    abandoned tab), the record is an orphan, not a real trip. My Trips/
    Landing should never see it."""
    return bool(record.trip_state.get("trip_context"))


@router.get("", response_model=TripListResponse)
async def list_trips(request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trips = await persistence.repository.list_trips(owner)
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
        _summary(t, has_recommendation=t.id in recommendation_ids) for t in populated_trips
    ])


@router.post("", response_model=TripResponse, status_code=201)
async def create_trip(payload: TripCreateRequest, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.create_trip(
        owner.guest_session_id, owner.user_id, payload.title, payload.product_mode, {"trip_context": payload.trip_context}, {}
    )
    logger.info("Created guest trip.", event="be.trip.created", source="http", trip_id=str(trip.id), version=trip.version)
    return _response(trip)


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


@router.get("/{trip_id}", response_model=TripResponse)
async def get_trip(trip_id: UUID, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
    if trip is None:
        logger.warning("Trip not found for guest.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    logger.info("Fetched guest trip.", event="be.trip.fetched", source="http", trip_id=str(trip_id), version=trip.version)
    return _response(trip)


@router.get("/{trip_id}/recommendations", response_model=TripRecommendationsResponse)
async def get_latest_recommendations(trip_id: UUID, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
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


@router.get("/{trip_id}/itinerary-versions", response_model=TripItineraryVersionsResponse)
async def list_itinerary_versions(trip_id: UUID, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
    if trip is None:
        logger.warning("Trip not found for guest.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    records = await persistence.repository.list_itinerary_versions(owner, trip_id)
    summaries = [
        TripItineraryVersionSummary(
            version=record.version,
            source_guide_revision=record.source_guide_revision,
            created_at=record.created_at,
            days=[
                ItineraryVersionDaySummary(day_number=day["day_number"], title=day["title"])
                for day in record.result["final_itinerary"]["days"]
            ],
        )
        for record in records
    ]
    logger.info(
        "Fetched archived itinerary versions.",
        event="be.trip.itinerary_versions.fetched",
        source="http",
        trip_id=str(trip_id),
        count=len(summaries),
    )
    return TripItineraryVersionsResponse(versions=summaries)


@router.get("/{trip_id}/itinerary", response_model=TripItineraryResponse)
async def get_current_itinerary(trip_id: UUID, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
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
    logger.info(
        "Fetched active itinerary.",
        event="be.trip.itinerary.fetched",
        source="http",
        trip_id=str(trip_id),
        found=True,
        version=current.version,
    )
    return TripItineraryResponse.model_validate(current, from_attributes=True)


@router.get("/{trip_id}/board", response_model=TripBoardResponse)
async def get_trip_board(
    trip_id: UUID,
    request: Request,
    response: Response,
    persistence: Persistence,
    logger: Logger,
    current_user: CurrentUser,
    trip_board: TripBoard,
):
    """TWM-202: one composed itinerary/booking item list — Atlas content
    merged with Trusted Actions feasibility for the itinerary's two gateway
    legs, computed once and shared by Overview and Itinerary instead of
    each screen deriving its own view."""
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
    if trip is None:
        logger.warning("Trip not found for guest.", event="be.trip.not_found", source="http", trip_id=str(trip_id))
        raise HTTPException(status_code=404, detail="Trip not found.")
    current = await persistence.repository.get_current_itinerary(owner, trip_id)
    if current is None:
        logger.info(
            "No active itinerary yet for trip board.",
            event="be.trip.board.fetched",
            source="http",
            trip_id=str(trip_id),
            found=False,
        )
        raise HTTPException(status_code=404, detail="No itinerary yet.")
    board = trip_board.build(
        trip_id=trip_id,
        version=current.version,
        final_itinerary=current.result["final_itinerary"],
        trip_context=trip.trip_state.get("trip_context") or {},
        booking_setup=trip.trip_state.get("booking_setup") or {},
    )
    logger.info(
        "Composed Trip Board.",
        event="be.trip.board.fetched",
        source="http",
        trip_id=str(trip_id),
        found=True,
        version=current.version,
        day_count=len(board.days),
    )
    return board


def _conflict(error: VersionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": "Trip has a newer version.", "current_version": error.current_version})


@router.patch("/{trip_id}", response_model=TripResponse)
async def rename_trip(trip_id: UUID, payload: TripRenameRequest, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
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
    return _response(trip)


@router.patch("/{trip_id}/ui-state", response_model=TripResponse)
async def update_ui_state(trip_id: UUID, payload: TripUiStateRequest, request: Request, response: Response, persistence: Persistence, logger: Logger, current_user: CurrentUser):
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
    current_user: CurrentUser,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
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
