"""Pure normalization of raw Aviasales entries into TWM-144's
NormalizedFlightOffer shape (TWM-145).

Deterministic and side-effect free: no network calls, no logging. A
malformed individual entry (missing a required field, or one that fails
NormalizedFlightOffer's own validators) is skipped rather than raising —
one bad cached entry must never fail the whole search.
"""

import hashlib
from datetime import datetime
from typing import Optional

from pydantic import ValidationError

from ...schemas.flight_search import (
    FlightBaggageAllowance,
    FlightFareConditions,
    FlightMoney,
    FlightProviderProvenance,
    FlightSearchRequest,
    NormalizedFlightOffer,
)
from .airlines import airline_name_for_code
from .calculations import compute_group_total_minor_units, traveler_total

PROVIDER_NAME = "aviasales"


def normalize_aviasales_offers(
    entries: list[dict],
    request: FlightSearchRequest,
    price_found_at: datetime,
    currency: str,
) -> list[NormalizedFlightOffer]:
    # TWM-215: None when the traveler's exact composition isn't known yet —
    # every offer then carries only the provider's own per-traveler price,
    # with no Backend-computed group total.
    traveler_count = traveler_total(request)
    offers: list[NormalizedFlightOffer] = []
    for entry in entries:
        offer = _normalize_one(entry, request, price_found_at, currency, traveler_count)
        if offer is not None:
            offers.append(offer)
    return offers


def _normalize_one(
    entry: dict,
    request: FlightSearchRequest,
    price_found_at: datetime,
    currency: str,
    traveler_count: Optional[int],
) -> Optional[NormalizedFlightOffer]:
    price = entry.get("price")
    airline = entry.get("airline")
    flight_number = entry.get("flight_number")
    departure_at = _parse_datetime(entry.get("departure_at"))
    if price is None or airline is None or flight_number is None or departure_at is None:
        return None

    return_at = _parse_datetime(entry.get("return_at"))
    return_date = None
    if request.trip_type == "round_trip":
        if return_at is None:
            # A round-trip offer requires a return_date; the provider did
            # not disclose one for this cached entry, so it cannot be
            # normalized rather than fabricating a value.
            return None
        return_date = return_at.date()

    per_traveler_amount_minor_units = round(float(price) * 100)
    # TWM-215: only compute a group total when the traveler count is
    # actually known -- these three FlightMoney fields are present together
    # or absent together (see its own validator).
    group_total_minor_units = (
        compute_group_total_minor_units(per_traveler_amount_minor_units, traveler_count)
        if traveler_count is not None
        else None
    )

    provider_reference = hashlib.sha256(
        f"{airline}-{flight_number}-{departure_at.isoformat()}".encode("utf-8")
    ).hexdigest()[:32]

    try:
        return NormalizedFlightOffer(
            origin_iata=request.origin_iata,
            destination_iata=request.destination_iata,
            trip_type=request.trip_type,
            departure_date=departure_at.date(),
            departure_at=departure_at,
            return_date=return_date,
            stop_count=None,
            airline_code=str(airline),
            airline_name=airline_name_for_code(str(airline)),
            flight_number=str(flight_number),
            money=FlightMoney(
                currency=currency,
                per_traveler_amount_minor_units=per_traveler_amount_minor_units,
                traveler_count=traveler_count,
                group_total_minor_units=group_total_minor_units,
                group_total_is_approximate=True if traveler_count is not None else None,
                tax_fee_included=None,
            ),
            baggage=FlightBaggageAllowance(),
            fare_conditions=FlightFareConditions(),
            provenance=FlightProviderProvenance(
                provider_name=PROVIDER_NAME,
                provider_reference=provider_reference,
            ),
            price_found_at=price_found_at,
            offer_expires_at=_parse_datetime(entry.get("expires_at")),
        )
    except ValidationError:
        return None


def _parse_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
