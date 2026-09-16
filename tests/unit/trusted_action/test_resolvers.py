"""Pure-function coverage for trusted-action SEARCH_REDIRECT URL/query
assembly. Operates only on plain primitives (see
``resolvers.build_query_params``/``tracking_params``), never a
TrustedActionRequest instance, per this repo's rule against instantiating
request/response schema models directly in unit tests.
"""

from datetime import date
from types import SimpleNamespace

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


def test_ixigo_train_exact_search_uses_path_not_query_params():
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
    assert params == {}


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


def test_non_travelpayouts_partners_omit_marker_when_unconfigured():
    assert tracking_params("booking_com", _NO_TRACKING) == {}


def test_redbus_never_carries_a_tracking_param():
    # redBus's confirmed EarnKaro program needs a link-wrapping integration
    # (see TWM_Docs/BOOKING_HANDOFF.md), not a query param -- appending one
    # to redBus's own URL would not earn commission, so this never happens.
    assert tracking_params("redbus", _WITH_TRACKING) == {}


def test_no_query_param_value_ever_looks_like_a_url():
    params = build_query_params(
        domain="stay",
        origin="Goa",
        destination="Coorg",
        departure_date=date(2026, 10, 1),
        return_date=None,
        trip_shape=None,
        traveler_count=1,
        partner="booking_com",
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
    ixigo = action_capability_metadata(_request_like(domain="stay", destination="Goa"), partner="ixigo")

    assert booking[0] == "prefilled_search"
    assert ixigo[0] == "destination_redirect"


def test_transport_capability_metadata_is_provider_specific():
    ixigo = action_capability_metadata(
        _request_like(domain="train", origin="Delhi", destination="Agra", departure_date=date(2026, 9, 10)),
        partner="ixigo",
    )
    redbus = action_capability_metadata(
        _request_like(domain="bus", origin="Delhi", destination="Agra", departure_date=date(2026, 9, 10)),
        partner="redbus",
    )

    assert ixigo == (
        "prefilled_search",
        "Search ixigo trains",
        "Station codes and date open prefilled on ixigo trains; confirm schedule, seats, and fare on ixigo.",
    )
    assert redbus == (
        "prefilled_search",
        "Search redBus",
        "Route and date open prefilled on redBus; confirm seats and fare on redBus.",
    )


def test_ixigo_flight_capability_is_evaluated_separately_from_ixigo_train():
    # TWM-230 Increment 2c: action_capability_metadata's ixigo branch must
    # check domain, not just partner -- ixigo now serves both train and
    # flight, and a flight request must never be evaluated against
    # station-code resolution (the train branch's logic).
    flight = action_capability_metadata(
        _request_like(domain="flight", origin="Delhi", destination="Mumbai", departure_date=date(2026, 9, 10)),
        partner="ixigo",
    )
    assert flight == (
        "prefilled_search",
        "Search ixigo flights",
        "Route, date, and traveler count open on ixigo when airport resolution succeeds.",
    )

    no_route = action_capability_metadata(
        _request_like(domain="flight", origin=None, destination=None, departure_date=date(2026, 9, 10)),
        partner="ixigo",
    )
    assert no_route[0] == "destination_search"


def test_aviasales_capability_is_prefilled_only_when_both_airports_resolve():
    resolvable = action_capability_metadata(
        _request_like(domain="flight", origin="Delhi", destination="Mumbai", departure_date=date(2026, 9, 10)),
        partner="aviasales",
    )
    assert resolvable[0] == "prefilled_search"


def test_aviasales_capability_degrades_without_a_route():
    # A date alone is not enough to prefill Aviasales -- _aviasales_query_params
    # needs a resolvable origin and destination to build origin_iata/
    # destination_iata. Claiming "prefilled_search" here would overpromise
    # exactly like the bug this test guards against.
    no_route = action_capability_metadata(
        _request_like(domain="flight", origin=None, destination=None, departure_date=date(2026, 9, 10)),
        partner="aviasales",
    )
    assert no_route[0] == "destination_search"

    unresolvable_route = action_capability_metadata(
        _request_like(domain="flight", origin="Nowhereville", destination="Nowhereland", departure_date=date(2026, 9, 10)),
        partner="aviasales",
    )
    assert unresolvable_route[0] == "destination_search"


def test_ixigo_train_uses_confirmed_station_code_path_for_exact_search():
    target = resolve_partner_target(
        _request_like(
            domain="train",
            origin="Delhi",
            destination="Agra",
            departure_date=date(2026, 9, 10),
        ),
        partner="ixigo",
        settings=_NO_TRACKING,
    )

    assert target.path == "trains/search-pwa/from/NDLS/to/AGC/10-09-2026"
    assert target.query_params == {}
    assert target.target_url == "https://www.ixigo.com/trains/search-pwa/from/NDLS/to/AGC/10-09-2026"


def test_ixigo_train_resolves_stations_from_the_bundled_dataset_not_a_hardcoded_table():
    # A hubless-town/railhead pair with no reason to be in any short curated
    # list -- proves station resolution comes from the bundled ~8,700-row
    # dataset (twm.services.station_resolution), not a hand-maintained
    # place -> code table scoped to a handful of demo cities.
    target = resolve_partner_target(
        _request_like(
            domain="train",
            origin="Bhubaneswar",
            destination="Pathankot",
            departure_date=date(2026, 9, 10),
        ),
        partner="ixigo",
        settings=_NO_TRACKING,
    )

    assert target.path == "trains/search-pwa/from/BBS/to/PTK/10-09-2026"
    assert target.query_params == {}


def test_ixigo_train_degrades_when_station_or_date_is_missing():
    target = resolve_partner_target(
        _request_like(domain="train", origin="Delhi", destination="Unknown City"),
        partner="ixigo",
        settings=_NO_TRACKING,
    )

    assert target.path == "trains"
    assert target.query_params == {"domain": "train", "origin": "Delhi", "destination": "Unknown City"}


def test_ixigo_flight_uses_confirmed_iata_query_shape_for_exact_search():
    target = resolve_partner_target(
        _request_like(
            domain="flight",
            origin="Delhi",
            destination="Mumbai",
            departure_date=date(2026, 9, 10),
            traveler_count=2,
        ),
        partner="ixigo",
        settings=_NO_TRACKING,
    )

    assert target.path == "search/result/flight"
    assert target.query_params == {
        "from": "DEL",
        "to": "BOM",
        "date": "10092026",
        "class": "e",
        "adults": "2",
        "children": "0",
        "infants": "0",
    }
    assert target.target_url == (
        "https://www.ixigo.com/search/result/flight"
        "?from=DEL&to=BOM&date=10092026&class=e&adults=2&children=0&infants=0"
    )


def test_ixigo_flight_round_trip_adds_return_date():
    target = resolve_partner_target(
        _request_like(
            domain="flight",
            origin="Delhi",
            destination="Mumbai",
            departure_date=date(2026, 9, 10),
            return_date=date(2026, 9, 17),
            trip_shape="round_trip",
        ),
        partner="ixigo",
        settings=_NO_TRACKING,
    )
    assert target.query_params["returnDate"] == "17092026"


def test_ixigo_flight_degrades_to_the_landing_page_without_a_scheduled_route():
    # ixigo's flight search-result path 404s without a resolvable route --
    # unlike trains, there is no bare, always-safe search surface there, so
    # this must fall back to the "flights" landing page, not a generic
    # query-param page under the search-result path (TWM-230 Increment 2c,
    # browser-verified).
    target = resolve_partner_target(
        _request_like(domain="flight", origin="Delhi", destination="Nowhereville"),
        partner="ixigo",
        settings=_NO_TRACKING,
    )
    assert target.path == "flights"
    assert target.query_params == {}
    assert target.target_url == "https://www.ixigo.com/flights"


def test_redbus_uses_confirmed_route_path_and_onward_date():
    target = resolve_partner_target(
        _request_like(domain="bus", origin="Delhi", destination="Manali", departure_date=date(2026, 9, 15)),
        partner="redbus",
        settings=_NO_TRACKING,
    )

    assert target.path == "bus-tickets/delhi-to-manali"
    assert target.query_params == {"onward": "15-Sep-2026"}
    assert target.target_url == "https://www.redbus.in/bus-tickets/delhi-to-manali?onward=15-Sep-2026"


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
    traveler_party=None,
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
    request.traveler_party = traveler_party
    return request


# --- Aviasales-specific query shape (TWM-196 P1 fix) -------------------------
# Travelpayouts' documented Aviasales search-form shape: origin_iata/
# destination_iata (Backend-resolved, never a raw city label when
# resolution succeeds), depart_date/return_date, one_way, adults/children/
# infants, trip_class, locale, plus the shared travelpayouts marker.


def test_aviasales_path_embeds_iata_date_and_passengers_for_exact_search():
    # TWM-230 Increment 2d: the real, browser-verified Aviasales shape
    # (Travelpayouts "Aviasales affiliate links" article) packs
    # origin+date+destination+passengers into the *path*, not query
    # params -- corrects the earlier origin_iata=/destination_iata= shape,
    # which was browser-verified broken (dropped every param).
    target = resolve_partner_target(
        _request_like(
            domain="flight",
            origin="Delhi",
            destination="Mumbai",
            departure_date=date(2026, 9, 10),
            traveler_count=3,
        ),
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert target.path == "search/DEL1009BOM3"
    assert target.query_params == {}
    assert target.target_url == "https://www.aviasales.com/search/DEL1009BOM3"


def test_aviasales_path_adds_return_date_segment_for_round_trip():
    target = resolve_partner_target(
        _request_like(
            domain="flight",
            origin="Delhi",
            destination="Mumbai",
            departure_date=date(2026, 9, 10),
            return_date=date(2026, 9, 17),
            trip_shape="round_trip",
            traveler_count=1,
        ),
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert target.path == "search/DEL1009BOM17091"


def test_aviasales_passenger_suffix_positional_encoding():
    # Adults digit always present; children digit present only if there
    # are children *or* infants (an infant digit needs a children
    # placeholder before it, even when children is 0); infants digit only
    # if there are infants. Mirrors the Travelpayouts-documented examples
    # exactly (e.g. "w101" = 1 adult, 0 children, 1 infant).
    base = dict(domain="flight", origin="Delhi", destination="Mumbai", departure_date=date(2026, 9, 10))
    party = lambda a, c, i: SimpleNamespace(adults=a, children=c, infants=i)  # noqa: E731
    one_adult = resolve_partner_target(_request_like(**base, traveler_party=party(1, 0, 0)), partner="aviasales", settings=_NO_TRACKING)
    two_adults_one_child = resolve_partner_target(_request_like(**base, traveler_party=party(2, 1, 0)), partner="aviasales", settings=_NO_TRACKING)
    one_adult_one_infant = resolve_partner_target(_request_like(**base, traveler_party=party(1, 0, 1)), partner="aviasales", settings=_NO_TRACKING)
    assert one_adult.path.endswith("BOM1")
    assert two_adults_one_child.path.endswith("BOM21")
    assert one_adult_one_infant.path.endswith("BOM101")


def test_aviasales_degrades_to_prefilled_form_when_date_is_missing():
    # The compact search-*results* path 404s without a date (browser-
    # verified); a resolved route with no date degrades to the root path
    # with a "params=" query carrying just the dateless route -- the
    # pre-filled *form*, not a broken results page.
    target = resolve_partner_target(
        _request_like(domain="flight", origin="Bangalore", destination="Bhubaneswar"),
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert target.path == "/"
    assert target.query_params == {"params": "BLRBBI1"}
    assert target.target_url == "https://www.aviasales.com/?params=BLRBBI1"


def test_aviasales_degrades_to_bare_homepage_when_airport_unresolvable():
    target = resolve_partner_target(
        _request_like(domain="flight", origin="Nowhereville", destination="Bhubaneswar", departure_date=date(2026, 9, 10)),
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert target.path == "/"
    assert target.query_params == {}
    assert target.target_url == "https://www.aviasales.com/"


def test_aviasales_degrades_for_a_non_scheduled_airstrip():
    # TWM-230 plausibility hardening: "Diego Garcia" resolves to a real
    # airport (NKW) but ourairports tags it scheduled_service=False -- a
    # non-scheduled airstrip's IATA code is as useless to a live Aviasales
    # search as no code at all, so this must degrade the same way an
    # unresolvable place does, not claim a working code.
    target = resolve_partner_target(
        _request_like(domain="flight", origin="Diego Garcia", destination="Bhubaneswar", departure_date=date(2026, 9, 10)),
        partner="aviasales",
        settings=_NO_TRACKING,
    )
    assert target.path == "/"
    assert target.query_params == {}


def test_aviasales_marker_is_attached_as_a_real_query_param_on_any_shape():
    # Browser-verified this session: appending ?marker=... on top of the
    # compact search-results path does not break the page.
    prefilled = resolve_partner_target(
        _request_like(domain="flight", origin="Delhi", destination="Mumbai", departure_date=date(2026, 9, 10)),
        partner="aviasales",
        settings=_WITH_TRACKING,
    )
    assert prefilled.query_params == {"marker": "marker-456"}
    degraded = resolve_partner_target(
        _request_like(domain="flight", origin="Nowhereville", destination="Bhubaneswar"),
        partner="aviasales",
        settings=_WITH_TRACKING,
    )
    assert degraded.query_params == {"marker": "marker-456"}


def test_aviasales_capability_degrades_for_a_non_scheduled_airstrip():
    degraded = action_capability_metadata(
        _request_like(domain="flight", origin="Diego Garcia", destination="Bhubaneswar", departure_date=date(2026, 9, 10)),
        partner="aviasales",
    )
    assert degraded[0] == "destination_search"
