"""Trusted-action resolution HTTP boundary (TWM-131).

Trip-owned, read/query-only — resolves the same owner model as
twm/routers/flight_search.py and never mutates trip lifecycle, plan, or
itinerary state.

Two endpoints, deliberately separate (see
twm/services/trusted_action/service.py's module docstring for why):

- ``POST /trips/{trip_id}/trusted-action``: resolves a single trusted
  action (CHECK_PRICES/PROVIDER/SEARCH_REDIRECT) from a
  ``TrustedActionRequest``.
- ``POST /trips/{trip_id}/trusted-action/feasibility``: a per-route
  feasibility assessment (flight/train/bus/drive), which has no field on
  ``TrustedActionResult`` to attach to and answers a structurally different
  question than resolving one action.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..dependencies import (
    get_current_user,
    get_logger,
    get_trip_persistence,
    get_trusted_action_service,
)
from ..persistence.contracts import User
from ..persistence.service import TripPersistenceService
from ..schemas.trusted_action import TrustedActionRequest, TrustedActionResult, TripFeasibilityAssessment
from ..schemas.trusted_action_feasibility import TripFeasibilityRequest
from ..services.trusted_action import TrustedActionService
from ..telemetry import TelemetryLogger
from .trips import _resolve_owner

router = APIRouter(prefix="/trips", tags=["Trusted Action"])

Persistence = Annotated[TripPersistenceService, Depends(get_trip_persistence)]
Logger = Annotated[TelemetryLogger, Depends(get_logger)]
CurrentUser = Annotated[User | None, Depends(get_current_user)]
TrustedAction = Annotated[TrustedActionService, Depends(get_trusted_action_service)]


@router.post("/{trip_id}/trusted-action", response_model=TrustedActionResult)
async def resolve_trusted_action(
    trip_id: UUID,
    payload: TrustedActionRequest,
    request: Request,
    response: Response,
    persistence: Persistence,
    logger: Logger,
    current_user: CurrentUser,
    trusted_action: TrustedAction,
):
    trip = await _get_owned_trip(trip_id, request, response, persistence, current_user, logger, "trusted_action")
    return trusted_action.resolve(trip_id, payload)


@router.post("/{trip_id}/trusted-action/feasibility", response_model=TripFeasibilityAssessment | None)
async def assess_trip_feasibility(
    trip_id: UUID,
    payload: TripFeasibilityRequest,
    request: Request,
    response: Response,
    persistence: Persistence,
    logger: Logger,
    current_user: CurrentUser,
    trusted_action: TrustedAction,
):
    await _get_owned_trip(trip_id, request, response, persistence, current_user, logger, "trusted_action_feasibility")
    return await trusted_action.assess_feasibility(trip_id, payload.origin, payload.destination)


async def _get_owned_trip(trip_id, request, response, persistence, current_user, logger, log_prefix):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
    if trip is None:
        logger.warning(
            "Trip not found for guest trusted-action request.",
            event=f"be.{log_prefix}.trip_not_found",
            source="http",
            trip_id=str(trip_id),
        )
        raise HTTPException(status_code=404, detail="Trip not found.")
    return trip
