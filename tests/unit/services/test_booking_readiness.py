"""Unit tests for the shared route-readiness policy (TWM-215) that
flight_search and trusted_action both compose their own
missing_required_fields from, instead of each independently re-deriving
it -- the exact duplication that let a fix land in one and not the other.
"""

from twm.services.booking_readiness import route_readiness


def test_reports_nothing_missing_for_a_complete_one_way_route():
    readiness = route_readiness(
        has_origin=True, has_destination=True, is_round_trip=False, has_return_date=False,
    )
    assert readiness.origin_missing is False
    assert readiness.destination_missing is False
    assert readiness.return_date_missing is False


def test_reports_origin_and_destination_missing():
    readiness = route_readiness(
        has_origin=False, has_destination=False, is_round_trip=False, has_return_date=False,
    )
    assert readiness.origin_missing is True
    assert readiness.destination_missing is True


def test_return_date_is_required_only_for_round_trip():
    one_way = route_readiness(
        has_origin=True, has_destination=True, is_round_trip=False, has_return_date=False,
    )
    round_trip = route_readiness(
        has_origin=True, has_destination=True, is_round_trip=True, has_return_date=False,
    )
    assert one_way.return_date_missing is False
    assert round_trip.return_date_missing is True


def test_round_trip_with_a_return_date_is_not_missing():
    readiness = route_readiness(
        has_origin=True, has_destination=True, is_round_trip=True, has_return_date=True,
    )
    assert readiness.return_date_missing is False


def test_a_domain_with_no_origin_concept_can_report_origin_as_always_present():
    # A stay/hotel search (trusted_action's own caller) has no origin
    # concept at all -- it passes has_origin=True unconditionally rather
    # than this function needing to know about domains.
    readiness = route_readiness(
        has_origin=True, has_destination=True, is_round_trip=False, has_return_date=False,
    )
    assert readiness.origin_missing is False
