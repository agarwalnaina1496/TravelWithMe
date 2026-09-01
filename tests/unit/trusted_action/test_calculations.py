"""Pure-function coverage for the trusted-action partner allowlist helper.
``missing_required_fields``/``resolve_partner`` operate on
TrustedActionRequest and are exercised through the FastAPI boundary
instead (tests/api/apitest_trusted_action.py), per this repo's rule
against instantiating request/response schema models directly in unit
tests. ``allowed_partners`` takes no schema input, so it is covered
directly here.
"""

from twm.services.trusted_action.calculations import allowed_partners


def test_flight_only_allows_aviasales():
    # TWM-196: flights use Aviasales/Travelpayouts for both the live-price
    # and affiliate-redirect paths, replacing the earlier ixigo placeholder.
    assert allowed_partners("flight") == ("aviasales",)


def test_train_only_allows_ixigo():
    assert allowed_partners("train") == ("ixigo",)


def test_bus_allows_ixigo_and_redbus():
    assert allowed_partners("bus") == ("ixigo", "redbus")


def test_stay_allows_current_stay_providers():
    assert allowed_partners("stay") == ("booking_com", "agoda", "ixigo")
