"""Deterministic filter/compute helpers for flight search (TWM-144).

Backend computes budget interpretation, group totals, and readiness itself
— provider price data is never trusted for money math, and no LLM ever
performs this arithmetic or decides readiness.
"""

from ...schemas.flight_search import (
    FlightExplanationCandidate,
    FlightSearchMissingField,
    FlightSearchRequest,
    NormalizedFlightOffer,
)


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


def rank_offers(offers: list[NormalizedFlightOffer]) -> list[NormalizedFlightOffer]:
    """Deterministic ranking default (TWM-146).

    Cheapest group_total_minor_units first — price is the primary signal
    travelers care about for a live-inventory shortlist, and it is the one
    field every offer always has (unlike stop_count, which the current
    provider generation frequently cannot disclose — see
    NormalizedFlightOffer.stop_count).

    Tie-break: fewer stops first when stop_count is known, offers with an
    unknown stop_count sort after offers with a known one at the same
    price (an unproven "fewer stops" claim should not outrank a proven
    one). This is a plain, explainable multi-key sort — not a weighted
    scoring model — matching this repo's "clear, explainable sort, never a
    black-box formula" instruction.

    Stable and deterministic: Python's sort is stable, and the sort key is
    a pure function of each offer's own fields, so the same input list
    always produces the same output order (ties preserve original —
    already price/dedupe-normalized — order).
    """

    def _sort_key(offer: NormalizedFlightOffer) -> tuple[int, int]:
        stop_rank = offer.stop_count if offer.stop_count is not None else 999
        return (offer.money.group_total_minor_units, stop_rank)

    ranked = sorted(offers, key=_sort_key)
    return [
        offer.model_copy(update={"is_recommended": index == 0})
        for index, offer in enumerate(ranked)
    ]


def build_explanation_candidates(
    offers: list[NormalizedFlightOffer],
) -> list[FlightExplanationCandidate]:
    """Pure mapping from ranked, sanitized offers to the bounded
    FlightExplanationCandidate shape (TWM-144's contract for a future LLM
    explanation step).

    No LLM is invoked here or anywhere in this story — this only prepares
    the bounded input a future story can wire a bounded, prompt-versioned
    explanation call against, consistent with TWM-131's precedent of
    leaving an equivalent seam unwired rather than building a partial
    agent-engine call.
    """

    candidates: list[FlightExplanationCandidate] = []
    for offer in offers:
        departure_window = offer.departure_at.isoformat() if offer.departure_at else offer.departure_date.isoformat()
        candidates.append(
            FlightExplanationCandidate(
                provider_name=offer.provenance.provider_name,
                origin_iata=offer.origin_iata,
                destination_iata=offer.destination_iata,
                stop_count=offer.stop_count,
                currency=offer.money.currency,
                group_total_minor_units=offer.money.group_total_minor_units,
                group_total_is_approximate=offer.money.group_total_is_approximate,
                departure_window=departure_window,
            )
        )
    return candidates
