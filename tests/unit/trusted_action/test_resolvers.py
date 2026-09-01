"""Pure-function coverage for trusted-action SEARCH_REDIRECT URL/query
assembly. Operates only on plain primitives (see
``resolvers.build_query_params``/``tracking_params``), never a
TrustedActionRequest instance, per this repo's rule against instantiating
request/response schema models directly in unit tests.
"""

from datetime import date

from twm.services.trusted_action.resolvers import (
    action_capability_metadata,
    _ixigo_destination_slug,
    build_query_params,
    partner_has_capability,
    resolve_partner_target,
    tracking_params,
)
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
    for partner in ("hotellook",):
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


def test_ixigo_stay_destination_slug_handles_common_destination_shapes():
    assert _ixigo_destination_slug("Goa") == "goa"
    assert _ixigo_destination_slug(" New Delhi ") == "new-delhi"
    assert _ixigo_destination_slug("McLeod Ganj!") == "mcleod-ganj"


def test_ixigo_stay_uses_native_hotel_destination_path_without_query_params():
    target = resolve_partner_target(
        _request_like(domain="stay", destination="New Delhi"),
        partner="ixigo",
        settings=_WITH_TRACKING,
    )
    assert target.path == "hotels/hotels-in-new-delhi"
    assert target.query_params == {}
    assert target.target_url == "https://www.ixigo.com/hotels/hotels-in-new-delhi"


def test_booking_stay_uses_confirmed_searchresults_shape():
    target = resolve_partner_target(
        _request_like(
            domain="stay",
            destination="Goa",
            departure_date=date(2026, 9, 15),
            return_date=date(2026, 9, 16),
            trip_shape="round_trip",
            traveler_count=2,
        ),
        partner="booking_com",
        settings=_WITH_TRACKING,
    )

    assert target.path == "searchresults.html"
    assert target.query_params == {
        "ss": "Goa",
        "no_rooms": "1",
        "group_children": "0",
        "selected_currency": "INR",
        "lang": "en-us",
        "checkin": "2026-09-15",
        "checkout": "2026-09-16",
        "group_adults": "2",
    }
    assert target.target_url.startswith("https://www.booking.com/searchresults.html?")
    assert "marker" not in target.query_params


def test_booking_stay_falls_back_to_destination_search_without_dates():
    target = resolve_partner_target(
        _request_like(domain="stay", destination="Goa"),
        partner="booking_com",
        settings=_NO_TRACKING,
    )

    assert target.query_params["ss"] == "Goa"
    assert "checkin" not in target.query_params
    assert "checkout" not in target.query_params


def test_agoda_stay_uses_known_city_metadata_for_exact_search():
    target = resolve_partner_target(
        _request_like(
            domain="stay",
            destination="Goa",
            departure_date=date(2026, 9, 15),
            return_date=date(2026, 9, 16),
            trip_shape="round_trip",
            traveler_count=2,
        ),
        partner="agoda",
        settings=_WITH_TRACKING,
    )

    assert target.path == "search"
    assert target.query_params == {
        "city": "11304",
        "rooms": "1",
        "children": "0",
        "locale": "en-us",
        "currency": "INR",
        "textToSearch": "Goa",
        "checkIn": "2026-09-15",
        "checkOut": "2026-09-16",
        "adults": "2",
    }
    assert "marker" not in target.query_params


def test_agoda_unknown_destination_has_no_confirmed_capability():
    request = _request_like(domain="stay", destination="Coorg")
    assert partner_has_capability(request, partner="agoda") is False


def test_stay_capability_metadata_is_provider_specific():
    booking = action_capability_metadata(
        _request_like(
            domain="stay",
            destination="Goa",
            departure_date=date(2026, 9, 15),
            return_date=date(2026, 9, 16),
            trip_shape="round_trip",
        ),
        partner="booking_com",
    )
    agoda = action_capability_metadata(_request_like(domain="stay", destination="Goa"), partner="agoda")
    ixigo = action_capability_metadata(_request_like(domain="stay", destination="Goa"), partner="ixigo")

    assert booking[0] == "prefilled_search"
    assert agoda[0] == "known_destination_search"
    assert ixigo[0] == "destination_redirect"


def test_ixigo_stay_slug_never_turns_url_like_text_into_url_syntax():
    target = resolve_partner_target(
        _request_like(domain="stay", destination="https://evil.example.com"),
        partner="ixigo",
        settings=_NO_TRACKING,
    )
    assert target.target_url == "https://www.ixigo.com/hotels/hotels-in-https-evil-example-com"
    assert "://" not in target.target_url[len("https://") :]


def _request_like(
    *,
    domain,
    origin=None,
    destination=None,
    departure_date=None,
    return_date=None,
    trip_shape="one_way",
    traveler_count=None,
):
    class RequestLike:
        pass

    request = RequestLike()
    request.domain = domain
    request.origin = origin
    request.destination = destination
    request.departure_date = departure_date
    request.return_date = return_date
    request.trip_shape = trip_shape
    request.traveler_count = traveler_count
    return request


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
