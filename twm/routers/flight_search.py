"""Provider-neutral live flight-search HTTP boundary (TWM-144).

Trip-owned, read/query-only — resolves the same owner model as the other
`/trips/{trip_id}/...` routes and never mutates trip lifecycle, plan, or
itinerary state (unlike `POST /trips/{trip_id}/commands`, see
twm/services/trip_commands/service.py). No flight-search provider is
integrated yet; see twm/services/flight_search/service.py for the current
(contract-only) behavior.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..dependencies import get_current_user, get_logger, get_trip_persistence
from ..persistence.contracts import User
from ..persistence.service import TripPersistenceService
from ..schemas.flight_search import FlightSearchRequest, FlightSearchResponse
from ..services.flight_search import FlightSearchService
from ..telemetry import TelemetryLogger
from .trips import _resolve_owner

router = APIRouter(prefix="/trips", tags=["Flight Search"])

Persistence = Annotated[TripPersistenceService, Depends(get_trip_persistence)]
Logger = Annotated[TelemetryLogger, Depends(get_logger)]
CurrentUser = Annotated[User | None, Depends(get_current_user)]


@router.post("/{trip_id}/flight-search", response_model=FlightSearchResponse)
async def search_flights(
    trip_id: UUID,
    payload: FlightSearchRequest,
    request: Request,
    response: Response,
    persistence: Persistence,
    logger: Logger,
    current_user: CurrentUser,
):
    owner = await _resolve_owner(request, response, persistence, current_user)
    trip = await persistence.repository.get_trip(owner, trip_id)
    if trip is None:
        logger.warning(
            "Trip not found for guest flight search.",
            event="be.flight_search.trip_not_found",
            source="http",
            trip_id=str(trip_id),
        )
        raise HTTPException(status_code=404, detail="Trip not found.")

    service = FlightSearchService(logger)
    return service.search(trip_id, payload)
