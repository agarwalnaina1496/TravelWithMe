"""Flight-search application service (TWM-144 contract, TWM-145 provider).

TWM-144 defines the provider-neutral request/readiness/offer contract for
live flight search and the trip-owned API boundary that validates a search
request deterministically. TWM-145 wires the Aviasales Data API
(AviasalesAdapter) as the concrete provider behind that contract.

When no provider is configured (adapter is None — see
FlightSearchSettings.load(), which never fails app startup on a missing
token), every syntactically-ready request still resolves to a typed
"unavailable, provider not configured" outcome, exactly as TWM-144 left it.
When a provider is configured, a ready request calls the adapter, and the
service normalizes, filters (max_stops / budget_ceiling), and dedupes
before ever returning a response — no LLM ever sees a raw provider
payload or performs this arithmetic.

This is a read/query boundary only: it never mutates trip lifecycle, plan,
selection, or itinerary state, and it never touches the trip's
optimistic-concurrency `version`.

Cache: TWM-144 defined FlightSearchCacheEntry's shape but did not wire a
live store. TWM-145 makes the explicit judgement call to continue
deferring a live cache/store for this story — every request (repeated or
not) calls the live provider. This is safe (Aviasales Data API is
itself server-side cached and has no documented per-second rate limit on
this tier, unlike the real-time Search API), but it is a known limitation:
a follow-up story should wire FlightSearchCacheEntry to reduce redundant
provider calls once traffic volume justifies it. Provider failure never
touches trip/plan/itinerary state — the service only ever returns a typed
FlightSearchResponse or (for a truly unexpected bug) lets an exception
propagate to FastAPI's existing generic error handling; it performs no
persistence writes of its own.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from ...schemas.flight_search import (
    FlightSearchClarification,
    FlightSearchRequest,
    FlightSearchResponse,
    FlightSearchUnavailableReason,
    NormalizedFlightOffer,
    ResolvedAirport,
)
from ...services.airport_resolution import resolve_airport
from ...telemetry import TelemetryLogger
from .calculations import (
    exceeds_budget_ceiling,
    exceeds_max_stops,
    missing_required_fields,
    rank_offers,
    resolve_date_precision,
)
from .errors import FlightProviderError, FlightProviderTimeoutError
from .normalization import normalize_aviasales_offers
from .aviasales import AviasalesAdapter

_CLARIFICATION_MESSAGE = (
    "Tell us the missing trip details so we can search for flights."
)
_PROVIDER_NOT_CONFIGURED_MESSAGE = (
    "Live flight search is not available yet for this trip."
)
_PROVIDER_UNAUTHORIZED_MESSAGE = (
    "Live flight search is temporarily unavailable."
)
_PROVIDER_TIMEOUT_MESSAGE = (
    "Live flight search took too long to respond. Try again shortly."
)
_PROVIDER_REJECTED_MESSAGE = (
    "Live flight search could not find any offers for this request."
)


@dataclass
class FlightSearchService:
    logger: TelemetryLogger
    adapter: Optional[AviasalesAdapter] = None
    currency: str = "USD"

    async def search(self, trip_id: UUID, payload: FlightSearchRequest) -> FlightSearchResponse:
        queried_at = datetime.now(timezone.utc)
        request_data = payload.model_dump(mode="json", exclude_none=True)
        self.logger.info(
            "Received live flight-search request.",
            event="be.flight_search.requested",
            source="application",
            trip_id=str(trip_id),
            payload=request_data,
        )

        origin_resolved, destination_resolved = self._resolve_airports(trip_id, payload)
        effective_payload = payload.model_copy(
            update={
                "origin_iata": payload.origin_iata or (origin_resolved.iata if origin_resolved else None),
                "destination_iata": payload.destination_iata
                or (destination_resolved.iata if destination_resolved else None),
            }
        )

        missing = missing_required_fields(effective_payload)
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
                origin_resolved=origin_resolved,
                destination_resolved=destination_resolved,
            )
        payload = effective_payload

        if self.adapter is None:
            self.logger.info(
                "No flight-search provider is configured; returning a typed "
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
                origin_resolved=origin_resolved,
                destination_resolved=destination_resolved,
                date_precision=resolve_date_precision(payload)[0],
            )

        date_precision, depart_date_param = resolve_date_precision(payload)
        self.logger.info(
            "Calling flight-search provider.",
            event="be.flight_search.provider_call_started",
            source="application",
            trip_id=str(trip_id),
            provider_name="aviasales",
            origin_iata=payload.origin_iata,
            destination_iata=payload.destination_iata,
            search_shape=payload.trip_type,
            date_precision=date_precision,
            depart_date=depart_date_param,
            return_date=payload.return_date,
        )
        try:
            entries, received_at = await self.adapter.fetch_cheapest_prices(
                origin_iata=payload.origin_iata,
                destination_iata=payload.destination_iata,
                depart_date=depart_date_param,
                return_date=payload.return_date,
                currency=self.currency,
            )
        except FlightProviderTimeoutError as error:
            self._log_provider_failure(trip_id, error)
            return self._unavailable(
                queried_at,
                "provider_timeout",
                _PROVIDER_TIMEOUT_MESSAGE,
                origin_resolved,
                destination_resolved,
                date_precision,
            )
        except FlightProviderError as error:
            self._log_provider_failure(trip_id, error)
            code = (
                "provider_unauthorized"
                if error.upstream_status_code in (401, 403)
                else "provider_rejected_request"
            )
            message = (
                _PROVIDER_UNAUTHORIZED_MESSAGE
                if code == "provider_unauthorized"
                else _PROVIDER_REJECTED_MESSAGE
            )
            return self._unavailable(
                queried_at,
                code,
                message,
                origin_resolved,
                destination_resolved,
                date_precision,
            )

        normalized = normalize_aviasales_offers(
            entries, payload, received_at, self.currency
        )
        if date_precision == "exact":
            normalized, stop_count_enriched = await self._enrich_stop_counts(
                normalized, payload
            )
        else:
            # /v1/prices/calendar (the stop-count enrichment source) is a
            # single-exact-date lookup — it has no month/flexible mode, so
            # enrichment is skipped rather than called with a fabricated
            # date.
            stop_count_enriched = False
        offers = _filter_and_dedupe(normalized, payload)
        offers = rank_offers(offers)

        self.logger.info(
            "Flight-search provider call completed.",
            event="be.flight_search.provider_call_completed",
            source="application",
            trip_id=str(trip_id),
            provider_name="aviasales",
            raw_offer_count=len(entries),
            normalized_offer_count=len(normalized),
            returned_offer_count=len(offers),
            stop_count_enriched=stop_count_enriched,
        )

        if not offers:
            return self._unavailable(
                queried_at,
                "provider_rejected_request",
                _PROVIDER_REJECTED_MESSAGE,
                origin_resolved,
                destination_resolved,
                date_precision,
            )

        status = "offer" if len(offers) == len(entries) else "partial"
        expiries = [offer.offer_expires_at for offer in offers if offer.offer_expires_at]
        return FlightSearchResponse(
            status=status,
            queried_at=queried_at,
            expires_at=min(expiries) if expiries else None,
            offers=offers,
            origin_resolved=origin_resolved,
            destination_resolved=destination_resolved,
            date_precision=date_precision,
        )

    def _resolve_airports(
        self, trip_id: UUID, payload: FlightSearchRequest
    ) -> tuple[Optional[ResolvedAirport], Optional[ResolvedAirport]]:
        """Resolve origin_place/destination_place to a validated IATA code
        (TWM-196). A caller-supplied origin_iata/destination_iata is never
        overridden — resolution only fills a gap the caller left for
        Backend to own. Every resolution outcome (found or not) is logged
        separately from provider-call logging so an unresolved/ambiguous
        airport is attributable at a glance rather than surfacing only as a
        generic missing_input clarification."""

        origin_resolved: Optional[ResolvedAirport] = None
        if payload.origin_iata is None and payload.origin_place:
            match = resolve_airport(payload.origin_place)
            if match is not None:
                origin_resolved = ResolvedAirport(
                    input_label=match.input_label,
                    iata=match.iata,
                    airport_name=match.airport_name,
                    source=match.source,
                    confidence=match.confidence,
                )
            self._log_airport_resolution(trip_id, payload.origin_place, origin_resolved)

        destination_resolved: Optional[ResolvedAirport] = None
        if payload.destination_iata is None and payload.destination_place:
            match = resolve_airport(payload.destination_place)
            if match is not None:
                destination_resolved = ResolvedAirport(
                    input_label=match.input_label,
                    iata=match.iata,
                    airport_name=match.airport_name,
                    source=match.source,
                    confidence=match.confidence,
                )
            self._log_airport_resolution(trip_id, payload.destination_place, destination_resolved)

        return origin_resolved, destination_resolved

    def _log_airport_resolution(
        self, trip_id: UUID, input_label: str, resolved: Optional[ResolvedAirport]
    ) -> None:
        if resolved is None:
            self.logger.info(
                "Could not resolve a place label to an airport.",
                event="be.airport_resolution.unresolved",
                source="application",
                trip_id=str(trip_id),
                input_label=input_label,
            )
            return
        self.logger.info(
            f"Resolved place label to airport {resolved.iata}.",
            event="be.airport_resolution.resolved",
            source="application",
            trip_id=str(trip_id),
            input_label=input_label,
            iata=resolved.iata,
            resolution_source=resolved.source,
            confidence=resolved.confidence,
        )

    async def _enrich_stop_counts(
        self, offers: list[NormalizedFlightOffer], payload: FlightSearchRequest
    ) -> tuple[list[NormalizedFlightOffer], bool]:
        """Best-effort stop-count enrichment via /v1/prices/calendar.

        /v1/prices/cheap (the primary lookup) never discloses a stop count.
        The calendar endpoint discloses one for a single cached entry per
        date, matched here to at most one of the primary candidates by
        (airline, flight_number). Any failure here (network, unmatched,
        malformed) is swallowed — enrichment is never a reason to fail or
        degrade the underlying search."""

        assert self.adapter is not None
        assert payload.departure_date is not None
        hint = await self.adapter.fetch_stop_count_hint(
            origin_iata=payload.origin_iata,
            destination_iata=payload.destination_iata,
            depart_date=payload.departure_date,
            currency=self.currency,
        )
        if hint is None:
            return offers, False

        hint_airline, hint_flight_number, hint_stop_count = hint
        enriched = False
        result: list[NormalizedFlightOffer] = []
        for offer in offers:
            if (
                not enriched
                and offer.stop_count is None
                and offer.airline_code == hint_airline
                and offer.flight_number == hint_flight_number
            ):
                offer = offer.model_copy(update={"stop_count": hint_stop_count})
                enriched = True
            result.append(offer)
        return result, enriched

    def _log_provider_failure(self, trip_id: UUID, error: FlightProviderError) -> None:
        self.logger.warning(
            "Flight-search provider call failed. Detail - "
            f"{error.error_type}: {error.detail}",
            event="be.flight_search.provider_call_failed",
            source="application",
            trip_id=str(trip_id),
            component=error.component,
            failure_stage=error.failure_stage,
            error_type=error.error_type,
            detail=error.detail,
            upstream_status_code=error.upstream_status_code,
        )

    def _unavailable(
        self,
        queried_at: datetime,
        code: str,
        message: str,
        origin_resolved: Optional[ResolvedAirport] = None,
        destination_resolved: Optional[ResolvedAirport] = None,
        date_precision: Optional[str] = None,
    ) -> FlightSearchResponse:
        return FlightSearchResponse(
            status="unavailable",
            queried_at=queried_at,
            unavailable=FlightSearchUnavailableReason(code=code, message=message),
            origin_resolved=origin_resolved,
            destination_resolved=destination_resolved,
            date_precision=date_precision,
        )


def _filter_and_dedupe(
    offers: list[NormalizedFlightOffer], payload: FlightSearchRequest
) -> list[NormalizedFlightOffer]:
    filtered = [
        offer for offer in offers if not exceeds_max_stops(offer.stop_count, payload.max_stops)
    ]
    budget_ceiling = payload.budget_ceiling
    if budget_ceiling is not None:
        filtered = [
            offer
            for offer in filtered
            if offer.money.currency != budget_ceiling.currency
            or not exceeds_budget_ceiling(
                offer.money.group_total_minor_units,
                budget_ceiling.group_total_minor_units_max,
            )
        ]

    seen: set[str] = set()
    deduped: list[NormalizedFlightOffer] = []
    for offer in filtered:
        key = offer.provenance.provider_reference
        if key in seen:
            continue
        seen.add(key)
        deduped.append(offer)
    return deduped
