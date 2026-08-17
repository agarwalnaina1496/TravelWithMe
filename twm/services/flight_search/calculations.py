"""Deterministic filter/compute helpers for flight search (TWM-144).

Backend computes budget interpretation, group totals, and readiness itself
— provider price data is never trusted for money math, and no LLM ever
performs this arithmetic or decides readiness.
"""

from ...schemas.flight_search import FlightSearchMissingField, FlightSearchRequest


def missing_required_fields(payload: FlightSearchRequest) -> list[FlightSearchMissingField]:
    """The deterministic set of missing/vague-precision inputs, if any.

    A caller (e.g. Scout/Meridian normalization upstream) is expected to
    have already collapsed a vague value ("sometime in September") down to
    "not yet known" (a None field) before calling this contract — this
    function only judges presence, never interprets fuzzy text.
    """

    missing: list[FlightSearchMissingField] = []
    if payload.origin_iata is None:
        missing.append("origin")
    if payload.destination_iata is None:
        missing.append("destination")
    if payload.departure_date is None:
        missing.append("departure_date")
    if payload.trip_type == "round_trip" and payload.return_date is None:
        missing.append("return_date")
    if payload.travelers is None:
        missing.append("travelers")
    return missing


def traveler_total(payload: FlightSearchRequest) -> int:
    """Total traveler count used only for the Backend-computed group total
    — never forwarded to a provider as a search filter."""

    travelers = payload.travelers
    if travelers is None:
        raise ValueError("traveler_total requires a fully-specified travelers count")
    return travelers.adults + travelers.children + travelers.infants


def compute_group_total_minor_units(
    per_traveler_amount_minor_units: int, traveler_count: int
) -> int:
    """price x travelers, not per-leg — the only group-total semantics this
    contract defines."""

    return per_traveler_amount_minor_units * traveler_count


def exceeds_budget_ceiling(group_total_minor_units: int, ceiling_minor_units: int) -> bool:
    return group_total_minor_units > ceiling_minor_units


def exceeds_max_stops(stop_count: int | None, max_stops: int | None) -> bool:
    if max_stops is None or stop_count is None:
        return False
    return stop_count > max_stops
