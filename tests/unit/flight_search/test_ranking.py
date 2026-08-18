"""Pure-function coverage for deterministic ranking and explanation-
candidate mapping (TWM-146).

rank_offers and build_explanation_candidates operate on
NormalizedFlightOffer instances rather than FlightSearchRequest, and are
themselves pure schema-model-free-logic helpers in the same style as
calculations.py's other helpers (compute_group_total_minor_units etc). This
repo's rule against instantiating request/response schema models directly
in unit tests targets asserting on FastAPI request/response *contract*
behavior (see test_calculations.py's docstring); constructing
NormalizedFlightOffer instances here is the direct-input fixture needed to
exercise these two pure functions, mirroring how normalization.py's own
output is asserted against in test_normalization.py.
"""

from datetime import datetime, timezone

from twm.schemas.flight_search import (
    FlightBaggageAllowance,
    FlightFareConditions,
    FlightMoney,
    FlightProviderProvenance,
    NormalizedFlightOffer,
)
from twm.services.flight_search.calculations import (
    build_explanation_candidates,
    rank_offers,
)

PRICE_FOUND_AT = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _offer(
    *,
    group_total_minor_units: int,
    stop_count: int | None = None,
    provider_reference: str = "ref-1",
    departure_at: datetime | None = None,
) -> NormalizedFlightOffer:
    return NormalizedFlightOffer(
        origin_iata="DEL",
        destination_iata="BOM",
        trip_type="one_way",
        departure_date="2026-09-10",
        departure_at=departure_at,
        return_date=None,
        stop_count=stop_count,
        airline_code="AI",
        airline_name="Air India",
        flight_number="101",
        money=FlightMoney(
            currency="USD",
            per_traveler_amount_minor_units=group_total_minor_units,
            traveler_count=1,
            group_total_minor_units=group_total_minor_units,
            group_total_is_approximate=True,
            tax_fee_included=None,
        ),
        baggage=FlightBaggageAllowance(),
        fare_conditions=FlightFareConditions(),
        provenance=FlightProviderProvenance(
            provider_name="aviasales", provider_reference=provider_reference
        ),
        price_found_at=PRICE_FOUND_AT,
        offer_expires_at=None,
    )


def test_rank_offers_orders_cheapest_first():
    offers = [
        _offer(group_total_minor_units=30_000, provider_reference="expensive"),
        _offer(group_total_minor_units=10_000, provider_reference="cheapest"),
        _offer(group_total_minor_units=20_000, provider_reference="middle"),
    ]

    ranked = rank_offers(offers)

    assert [o.provenance.provider_reference for o in ranked] == [
        "cheapest",
        "middle",
        "expensive",
    ]


def test_rank_offers_marks_only_the_top_offer_as_recommended():
    offers = [
        _offer(group_total_minor_units=30_000, provider_reference="expensive"),
        _offer(group_total_minor_units=10_000, provider_reference="cheapest"),
    ]

    ranked = rank_offers(offers)

    assert ranked[0].is_recommended is True
    assert ranked[1].is_recommended is False


def test_rank_offers_ties_break_on_fewer_stops():
    offers = [
        _offer(group_total_minor_units=10_000, stop_count=2, provider_reference="two_stops"),
        _offer(group_total_minor_units=10_000, stop_count=0, provider_reference="direct"),
        _offer(group_total_minor_units=10_000, stop_count=1, provider_reference="one_stop"),
    ]

    ranked = rank_offers(offers)

    assert [o.provenance.provider_reference for o in ranked] == [
        "direct",
        "one_stop",
        "two_stops",
    ]


def test_rank_offers_unknown_stop_count_sorts_after_known_stop_count_at_same_price():
    offers = [
        _offer(group_total_minor_units=10_000, stop_count=None, provider_reference="unknown"),
        _offer(group_total_minor_units=10_000, stop_count=3, provider_reference="known"),
    ]

    ranked = rank_offers(offers)

    assert [o.provenance.provider_reference for o in ranked] == ["known", "unknown"]


def test_rank_offers_is_deterministic_for_the_same_input():
    offers = [
        _offer(group_total_minor_units=30_000, provider_reference="a"),
        _offer(group_total_minor_units=10_000, provider_reference="b"),
        _offer(group_total_minor_units=20_000, provider_reference="c"),
    ]

    first = [o.provenance.provider_reference for o in rank_offers(offers)]
    second = [o.provenance.provider_reference for o in rank_offers(offers)]

    assert first == second


def test_rank_offers_does_not_mutate_the_input_list_order():
    offers = [
        _offer(group_total_minor_units=30_000, provider_reference="a"),
        _offer(group_total_minor_units=10_000, provider_reference="b"),
    ]
    original_order = [o.provenance.provider_reference for o in offers]

    rank_offers(offers)

    assert [o.provenance.provider_reference for o in offers] == original_order


def test_rank_offers_empty_list_returns_empty_list():
    assert rank_offers([]) == []


def test_build_explanation_candidates_maps_bounded_fields_only():
    departure_at = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)
    offer = _offer(
        group_total_minor_units=10_000,
        stop_count=1,
        provider_reference="ref-1",
        departure_at=departure_at,
    )

    candidates = build_explanation_candidates([offer])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.provider_name == "aviasales"
    assert candidate.origin_iata == "DEL"
    assert candidate.destination_iata == "BOM"
    assert candidate.stop_count == 1
    assert candidate.currency == "USD"
    assert candidate.group_total_minor_units == 10_000
    assert candidate.group_total_is_approximate is True
    assert candidate.departure_window == departure_at.isoformat()


def test_build_explanation_candidates_falls_back_to_departure_date_when_no_time_disclosed():
    offer = _offer(group_total_minor_units=10_000, departure_at=None)

    candidates = build_explanation_candidates([offer])

    assert candidates[0].departure_window == "2026-09-10"


def test_build_explanation_candidates_carries_no_raw_payload_or_credentials():
    offer = _offer(group_total_minor_units=10_000)

    candidates = build_explanation_candidates([offer])

    dumped = candidates[0].model_dump()
    assert "provenance" not in dumped
    assert "provider_reference" not in dumped
    assert "url" not in dumped
    assert set(dumped.keys()) == {
        "provider_name",
        "origin_iata",
        "destination_iata",
        "stop_count",
        "currency",
        "group_total_minor_units",
        "group_total_is_approximate",
        "departure_window",
    }


def test_build_explanation_candidates_empty_list_returns_empty_list():
    assert build_explanation_candidates([]) == []
