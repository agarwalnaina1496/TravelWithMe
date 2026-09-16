"""Direct unit coverage for the extracted Aviasales deep-link helpers
(TWM-230 Increment 2d)."""

from datetime import date

from twm.services.trusted_action.aviasales_link import (
    aviasales_form_params,
    aviasales_passenger_suffix,
    aviasales_route_segment,
)


def test_passenger_suffix_matches_travelpayouts_documented_examples():
    assert aviasales_passenger_suffix((1, 0, 0)) == "1"
    assert aviasales_passenger_suffix((2, 1, 0)) == "21"
    assert aviasales_passenger_suffix((1, 0, 1)) == "101"
    assert aviasales_passenger_suffix((3, 2, 1)) == "321"


def test_passenger_suffix_defaults_to_one_adult_when_occupancy_unknown():
    assert aviasales_passenger_suffix(None) == "1"


def test_route_segment_one_way():
    assert aviasales_route_segment("DEL", "BOM", date(2026, 9, 10), None, "one_way") == "DEL1009BOM"


def test_route_segment_round_trip_appends_return_date():
    assert (
        aviasales_route_segment("DEL", "BOM", date(2026, 9, 10), date(2026, 9, 17), "round_trip")
        == "DEL1009BOM1709"
    )


def test_route_segment_omits_dates_entirely_when_departure_date_is_none():
    assert aviasales_route_segment("DEL", "BOM", None, None, "one_way") == "DELBOM"


def test_form_params_carries_the_dateless_route():
    assert aviasales_form_params(
        origin_iata="BLR", destination_iata="BBI", departure_date=None, occupancy=(2, 0, 0)
    ) == {"params": "BLRBBI2"}


def test_form_params_empty_when_a_date_is_present():
    # The search-results path (built elsewhere) handles the dated case --
    # this fallback is dateless-only.
    assert aviasales_form_params(
        origin_iata="BLR", destination_iata="BBI", departure_date=date(2026, 9, 10), occupancy=None
    ) == {}


def test_form_params_empty_when_route_does_not_resolve():
    assert aviasales_form_params(
        origin_iata=None, destination_iata="BBI", departure_date=None, occupancy=None
    ) == {}
