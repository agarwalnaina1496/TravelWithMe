"""Pure-function coverage for the trusted-action partner allowlist helper.
``missing_required_fields``/``resolve_partner`` operate on
TrustedActionRequest and are exercised through the FastAPI boundary
instead (tests/api/apitest_trusted_action.py), per this repo's rule
against instantiating request/response schema models directly in unit
tests. ``allowed_partners`` takes no schema input, so it is covered
directly here.
"""

from twm.services.trusted_action.calculations import allowed_partners


def test_flight_allows_aviasales_and_ixigo():
    # TWM-196: the live-price path stays Aviasales-only (CHECK_PRICES).
    # TWM-230 Increment 2c: the SEARCH_REDIRECT fallback also approves
    # ixigo, which has its own confirmed flight search deep link.
    assert allowed_partners("flight") == ("aviasales", "ixigo")


def test_train_only_allows_ixigo():
    assert allowed_partners("train") == ("ixigo",)


def test_bus_only_allows_redbus():
    assert allowed_partners("bus") == ("redbus",)


def test_stay_allows_current_stay_providers():
    assert allowed_partners("stay") == ("booking_com", "ixigo")
