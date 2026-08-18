"""Pure-function coverage for deterministic flight-search compute helpers.

Only the primitive, schema-model-free helpers are covered directly here
(compute_group_total_minor_units / exceeds_budget_ceiling /
exceeds_max_stops). missing_required_fields and traveler_total operate on
FlightSearchRequest and are exercised through the FastAPI boundary instead
(tests/api/apitest_flight_search.py), per this repo's rule against
instantiating request/response schema models directly in unit tests.
"""

from twm.services.flight_search.calculations import (
    compute_group_total_minor_units,
    exceeds_budget_ceiling,
    exceeds_max_stops,
)


def test_group_total_is_price_times_travelers_not_per_leg():
    assert compute_group_total_minor_units(12_000, 3) == 36_000


def test_group_total_for_single_traveler_equals_per_traveler_amount():
    assert compute_group_total_minor_units(5_000, 1) == 5_000


def test_group_total_for_zero_amount_is_zero():
    assert compute_group_total_minor_units(0, 4) == 0


def test_exceeds_budget_ceiling_true_when_over():
    assert exceeds_budget_ceiling(36_000, 30_000) is True


def test_exceeds_budget_ceiling_false_when_within():
    assert exceeds_budget_ceiling(30_000, 30_000) is False
    assert exceeds_budget_ceiling(20_000, 30_000) is False


def test_exceeds_max_stops_true_when_over_the_limit():
    assert exceeds_max_stops(2, 1) is True


def test_exceeds_max_stops_false_when_within_the_limit():
    assert exceeds_max_stops(1, 1) is False
    assert exceeds_max_stops(0, 1) is False


def test_exceeds_max_stops_never_filters_when_either_side_is_unknown():
    assert exceeds_max_stops(None, 1) is False
    assert exceeds_max_stops(3, None) is False
    assert exceeds_max_stops(None, None) is False
