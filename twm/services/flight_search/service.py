"""Flight-search application service (TWM-144, contract-only).

TWM-144 defines the provider-neutral request/readiness/offer contract for
live flight search and the trip-owned API boundary that validates a search
request deterministically. No flight-search provider is integrated yet —
TWM-145 (blocked by this story) selects and wires a provider (the
Travelpayouts Data API — see twm/schemas/flight_search.py for why) against
this contract.

Until a provider exists, every syntactically-ready search request
deterministically resolves to a typed "unavailable, provider not
configured" outcome. It never fabricates offers, never guesses a route or
date, and never calls out to any external system. This is a read/query
boundary only: it never mutates trip lifecycle, plan, selection, or
itinerary state, and it never touches the trip's optimistic-concurrency
`version` (see twm/services/trip_commands for what a *mutating* command
looks like — this is intentionally not that shape).

Idempotency: this service performs no persistence writes at all in this
story (no cache is wired — see FlightSearchCacheEntry's docstring), so a
repeated identical request is trivially safe: it recomputes the same
deterministic outcome and never duplicates a cache entry or double-charges
provider quota, because nothing is written or called.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from ...schemas.flight_search import (
    FlightSearchClarification,
    FlightSearchRequest,
    FlightSearchResponse,
    FlightSearchUnavailableReason,
)
from ...telemetry import TelemetryLogger
from .calculations import missing_required_fields

_CLARIFICATION_MESSAGE = (
    "Tell us the missing trip details so we can search for flights."
)
_PROVIDER_NOT_CONFIGURED_MESSAGE = (
    "Live flight search is not available yet for this trip."
)


@dataclass
class FlightSearchService:
    logger: TelemetryLogger

    def search(self, trip_id: UUID, payload: FlightSearchRequest) -> FlightSearchResponse:
        queried_at = datetime.now(timezone.utc)
        request_data = payload.model_dump(mode="json", exclude_none=True)
        self.logger.info(
            "Received live flight-search request.",
            event="be.flight_search.requested",
            source="application",
            trip_id=str(trip_id),
            payload=request_data,
        )

        missing = missing_required_fields(payload)
        if missing:
            self.logger.info(
                "Rejected flight-search request pending traveler clarification.",
                event="be.flight_search.readiness_rejected",
                source="application",
                trip_id=str(trip_id),
                missing_fields=missing,
            )
            return FlightSearchResponse(
                status="clarification_needed",
                queried_at=queried_at,
                clarification=FlightSearchClarification(
                    missing_fields=missing, message=_CLARIFICATION_MESSAGE
                ),
            )

        self.logger.info(
            "No flight-search provider is configured yet; returning a typed "
            "not-yet-available outcome.",
            event="be.flight_search.unavailable",
            source="application",
            trip_id=str(trip_id),
            reason_code="provider_not_configured",
        )
        return FlightSearchResponse(
            status="unavailable",
            queried_at=queried_at,
            unavailable=FlightSearchUnavailableReason(
                code="provider_not_configured",
                message=_PROVIDER_NOT_CONFIGURED_MESSAGE,
            ),
        )
