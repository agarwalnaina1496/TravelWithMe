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
        trip_shape="round_trip",
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
        trip_shape=None,
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
        trip_shape=None,
        traveler_count=1,
        partner="hotellook",
        settings=_WITH_TRACKING,
    )
    for value in params.values():
        assert "://" not in value
        assert not value.startswith("//")


# --- Aviasales-specific query shape (TWM-196 P1 fix) -------------------------
# Travelpayouts' documented Aviasales search-form shape: origin_iata/
# destination_iata (Backend-resolved, never a raw city label when
# resolution succeeds), depart_date/return_date, one_way, adults/children/
# infants, trip_class, locale, plus the shared travelpayouts marker.


def test_aviasales_params_resolve_known_cities_to_iata():
    params = build_query_params(
        domain="flight",
        origin="Bangalore",
        destination="Bhubaneswar",
        departure_date=date(2026, 9, 10),
        return_date=None,
        trip_shape="one_way",
        traveler_count=2,
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert params["origin_iata"] == "BLR"
    assert params["destination_iata"] == "BBI"
    assert "origin" not in params
    assert "destination" not in params


def test_aviasales_params_include_exact_date_route_shape_and_passengers():
    params = build_query_params(
        domain="flight",
        origin="Delhi",
        destination="Mumbai",
        departure_date=date(2026, 9, 10),
        return_date=date(2026, 9, 17),
        trip_shape="round_trip",
        traveler_count=3,
        partner="aviasales",
        settings=_WITH_TRACKING,
    )
    assert params == {
        "origin_iata": "DEL",
        "destination_iata": "BOM",
        "depart_date": "2026-09-10",
        "return_date": "2026-09-17",
        "one_way": "false",
        "adults": "3",
        "children": "0",
        "infants": "0",
        "trip_class": "0",
        "locale": "en",
        "marker": "marker-456",
    }


def test_aviasales_params_omit_dates_for_a_flexible_no_date_search():
    params = build_query_params(
        domain="flight",
        origin="Bangalore",
        destination="Bhubaneswar",
        departure_date=None,
        return_date=None,
        trip_shape="one_way",
        traveler_count=1,
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert "depart_date" not in params
    assert "return_date" not in params
    assert params["one_way"] == "true"


def test_aviasales_params_omit_passenger_fields_when_traveler_count_unknown():
    params = build_query_params(
        domain="flight",
        origin="Bangalore",
        destination="Bhubaneswar",
        departure_date=None,
        return_date=None,
        trip_shape="one_way",
        traveler_count=None,
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert "adults" not in params
    assert "children" not in params
    assert "infants" not in params


def test_aviasales_params_fall_back_to_raw_label_when_airport_unresolvable():
    params = build_query_params(
        domain="flight",
        origin="Nowhereville",
        destination="Bhubaneswar",
        departure_date=None,
        return_date=None,
        trip_shape="one_way",
        traveler_count=1,
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert params["origin"] == "Nowhereville"
    assert "origin_iata" not in params
    assert params["destination_iata"] == "BBI"
