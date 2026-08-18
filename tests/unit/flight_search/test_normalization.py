"""Normalization tests for the Aviasales adapter output (TWM-145)."""

from datetime import datetime, timezone

from twm.schemas.flight_search import FlightSearchRequest, FlightSearchTravelerCount
from twm.services.flight_search.normalization import normalize_aviasales_offers

RECEIVED_AT = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _round_trip_request(**overrides) -> FlightSearchRequest:
    defaults = dict(
        origin_iata="DEL",
        destination_iata="BOM",
        trip_type="round_trip",
        departure_date="2026-09-10",
        return_date="2026-09-17",
        travelers=FlightSearchTravelerCount(adults=2, children=0, infants=0),
    )
    defaults.update(overrides)
    return FlightSearchRequest(**defaults)


def _entry(**overrides) -> dict:
    defaults = dict(
        price=5000,
        airline="AI",
        flight_number=101,
        departure_at="2026-09-10T10:00:00+00:00",
        return_at="2026-09-17T10:00:00+00:00",
        expires_at="2026-08-20T00:00:00+00:00",
    )
    defaults.update(overrides)
    return defaults


def test_normalizes_a_valid_entry_with_correct_group_total_math() -> None:
    request = _round_trip_request()
    offers = normalize_aviasales_offers([_entry()], request, RECEIVED_AT, "USD")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.money.currency == "USD"
    assert offer.money.per_traveler_amount_minor_units == 500_000
    assert offer.money.traveler_count == 2
    assert offer.money.group_total_minor_units == 1_000_000
    assert offer.money.group_total_is_approximate is True
    assert offer.money.tax_fee_included is None
    assert offer.baggage.checked_bag_included is None
    assert offer.baggage.cabin_bag_included is None
    assert offer.fare_conditions.refundable is None
    assert offer.fare_conditions.changeable is None
    assert offer.provenance.provider_name == "aviasales"
    assert offer.stop_count is None
    assert offer.price_found_at == RECEIVED_AT
    assert offer.offer_expires_at is not None
    assert offer.departure_date.isoformat() == "2026-09-10"
    assert offer.return_date.isoformat() == "2026-09-17"
    assert offer.departure_at.isoformat() == "2026-09-10T10:00:00+00:00"
    assert offer.airline_code == "AI"
    assert offer.airline_name == "Air India"
    assert offer.flight_number == "101"


def test_unknown_airline_code_yields_no_airline_name_not_a_guess() -> None:
    request = _round_trip_request()
    entry = _entry(airline="ZZ")
    offers = normalize_aviasales_offers([entry], request, RECEIVED_AT, "USD")

    assert len(offers) == 1
    assert offers[0].airline_code == "ZZ"
    assert offers[0].airline_name is None


def test_provider_reference_is_opaque_stable_and_never_a_url() -> None:
    request = _round_trip_request()
    offers_a = normalize_aviasales_offers([_entry()], request, RECEIVED_AT, "USD")
    offers_b = normalize_aviasales_offers([_entry()], request, RECEIVED_AT, "USD")

    reference = offers_a[0].provenance.provider_reference
    assert reference == offers_b[0].provenance.provider_reference
    assert "http" not in reference
    assert reference != "AI-101-2026-09-10T10:00:00+00:00"


def test_round_trip_entry_missing_return_at_is_skipped_not_fabricated() -> None:
    request = _round_trip_request()
    entry = _entry(return_at=None)
    offers = normalize_aviasales_offers([entry], request, RECEIVED_AT, "USD")

    assert offers == []


def test_entry_missing_required_field_is_skipped() -> None:
    request = _round_trip_request()
    entry = _entry(price=None)
    offers = normalize_aviasales_offers([entry], request, RECEIVED_AT, "USD")

    assert offers == []


def test_one_way_request_ignores_return_at_and_produces_one_way_offer() -> None:
    request = FlightSearchRequest(
        origin_iata="DEL",
        destination_iata="BOM",
        trip_type="one_way",
        departure_date="2026-09-10",
        travelers=FlightSearchTravelerCount(adults=1, children=0, infants=0),
    )
    entry = _entry(return_at=None)
    offers = normalize_aviasales_offers([entry], request, RECEIVED_AT, "USD")

    assert len(offers) == 1
    assert offers[0].trip_type == "one_way"
    assert offers[0].return_date is None
