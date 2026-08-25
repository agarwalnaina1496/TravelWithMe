"""Pure-function coverage for trusted-action SEARCH_REDIRECT URL/query
assembly. Operates only on plain primitives (see
``resolvers.build_query_params``/``tracking_params``), never a
TrustedActionRequest instance, per this repo's rule against instantiating
request/response schema models directly in unit tests.
"""

from datetime import date

from twm.services.trusted_action.resolvers import build_query_params, tracking_params
from twm.services.trusted_action.settings import TrustedActionSettings

_NO_TRACKING = TrustedActionSettings(ixigo_affiliate_id=None, travelpayouts_marker=None)
_WITH_TRACKING = TrustedActionSettings(ixigo_affiliate_id="ek-123", travelpayouts_marker="marker-456")


def test_query_params_include_route_dates_and_travelers():
    params = build_query_params(
        domain="train",
        origin="Delhi",
        destination="Agra",
        departure_date=date(2026, 9, 10),
        return_date=date(2026, 9, 17),
        traveler_count=2,
        partner="ixigo",
        settings=_NO_TRACKING,
    )
    assert params["domain"] == "train"
    assert params["origin"] == "Delhi"
    assert params["destination"] == "Agra"
    assert params["depart_date"] == "2026-09-10"
    assert params["return_date"] == "2026-09-17"
    assert params["travelers"] == "2"


def test_query_params_omit_absent_optional_fields():
    params = build_query_params(
        domain="bus",
        origin="Kochi",
        destination="Alleppey",
        departure_date=None,
        return_date=None,
        traveler_count=None,
        partner="redbus",
        settings=_NO_TRACKING,
    )
    assert "depart_date" not in params
    assert "return_date" not in params
    assert "travelers" not in params


def test_ixigo_tracking_omitted_when_affiliate_id_unset():
    assert tracking_params("ixigo", _NO_TRACKING) == {}


def test_ixigo_tracking_included_when_affiliate_id_configured():
    assert tracking_params("ixigo", _WITH_TRACKING) == {"affiliate_id": "ek-123"}


def test_aviasales_tracking_uses_the_travelpayouts_marker_not_ixigo(
) -> None:
    # TWM-196: flight's SEARCH_REDIRECT partner (Aviasales) is a
    # Travelpayouts brand, same tracking identity as the live-price path —
    # never ixigo's separate affiliate id, even when both are configured.
    assert tracking_params("aviasales", _WITH_TRACKING) == {"marker": "marker-456"}


def test_aviasales_tracking_omitted_when_marker_unset():
    assert tracking_params("aviasales", _NO_TRACKING) == {}


def test_travelpayouts_partners_get_marker_when_configured():
    for partner in ("hotellook", "booking_com", "agoda"):
        assert tracking_params(partner, _WITH_TRACKING) == {"marker": "marker-456"}


def test_travelpayouts_partners_omit_marker_when_unconfigured():
    for partner in ("hotellook", "booking_com", "agoda"):
        assert tracking_params(partner, _NO_TRACKING) == {}


def test_redbus_and_hostelworld_never_carry_a_tracking_param():
    assert tracking_params("redbus", _WITH_TRACKING) == {}
    assert tracking_params("hostelworld", _WITH_TRACKING) == {}


def test_no_query_param_value_ever_looks_like_a_url():
    params = build_query_params(
        domain="stay",
        origin="Goa",
        destination="Coorg",
        departure_date=date(2026, 10, 1),
        return_date=None,
        traveler_count=1,
        partner="hotellook",
        settings=_WITH_TRACKING,
    )
    for value in params.values():
        assert "://" not in value
        assert not value.startswith("//")
