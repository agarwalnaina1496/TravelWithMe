"""Pure-function coverage for the trusted-action partner allowlist helper.
``missing_required_fields``/``resolve_partner`` operate on
TrustedActionRequest and are exercised through the FastAPI boundary
instead (tests/api/apitest_trusted_action.py), per this repo's rule
against instantiating request/response schema models directly in unit
tests. ``allowed_partners`` takes no schema input, so it is covered
directly here.
"""

from twm.services.trusted_action.calculations import allowed_partners


def test_flight_only_allows_ixigo():
    assert allowed_partners("flight") == ("ixigo",)


def test_train_only_allows_ixigo():
    assert allowed_partners("train") == ("ixigo",)


def test_bus_allows_ixigo_and_redbus():
    assert allowed_partners("bus") == ("ixigo", "redbus")


def test_stay_allows_all_stay_partners():
    assert allowed_partners("stay") == ("hotellook", "booking_com", "agoda", "hostelworld", "ixigo")
