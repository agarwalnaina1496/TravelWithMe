"""TWM-217: the enriched /itinerary — item identity, resolved dates, stay
segments, and gateway transport options."""

from uuid import uuid4

from twm.services.trip_view.itinerary_enrichment import _hub_long_haul_endpoints, enrich_itinerary
from twm.services.trip_view.trip_dates import compose_trip_dates
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings

TRIP_ID = uuid4()


def _logger():
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test",
            payload_mode=PayloadMode.METADATA, max_field_size=256,
        ),
        sink,
    )
    return logger, sink


def _travel(from_city, to_city, hubs=None):
    item = {"kind": "TRAVEL", "title": f"{from_city} to {to_city}", "location": f"{from_city} to {to_city}",
            "detail": "d", "from_city": from_city, "to_city": to_city, "reference": {"status": "GENERAL_GUIDANCE"}}
    if hubs is not None:
        item["hubs"] = hubs
    return item


def _stay(location):
    return {"kind": "STAY", "title": "Overnight", "location": location, "detail": "d",
            "reference": {"status": "GENERAL_GUIDANCE"}}


def _day(n, timeline, primary="Jaipur"):
    return {"day_number": n, "title": f"Day {n}", "primary_location": primary, "summary": "s",
            "timeline": timeline, "notes": []}


def _itinerary(days):
    return {"trip_summary": {"title": "t", "destinations": ["Jaipur"], "trip_duration": len(days),
                             "overview": "o", "route_rationale": "r"}, "days": days,
            "budget_summary": {"currency": "INR", "lines": [{"category": "x", "amount_low": 1,
                               "amount_high": 2, "note": "n"}], "total_low": 1, "total_high": 2},
            "practical_notes": [], "sources": [], "assumptions": []}


def _enrich(days, *, trip_context=None, booking_setup=None, travel_dates=None, logger=None):
    ctx = trip_context if trip_context is not None else {"origin_city": "Delhi"}
    if travel_dates:
        ctx = {**ctx, "travel_dates": travel_dates}
    return enrich_itinerary(
        TRIP_ID, _itinerary(days), ctx, booking_setup or {},
        compose_trip_dates(ctx, len(days)),
        logger or _logger()[0],
    )


def test_every_timeline_item_gets_a_stable_id():
    days = [_day(1, [_travel("Delhi", "Jaipur"), _stay("Jaipur")])]
    first = _enrich(days)
    second = _enrich(days)
    ids = [i["id"] for i in first["days"][0]["timeline"]]
    assert ids == [f"{TRIP_ID}:1:0", f"{TRIP_ID}:1:1"]
    assert ids == [i["id"] for i in second["days"][0]["timeline"]]


def test_gateway_legs_are_flagged_internal_legs_are_not():
    days = [
        _day(1, [_travel("Delhi", "Jaipur")]),
        _day(2, [_travel("Jaipur", "Agra")]),
        _day(3, [_travel("Agra", "Delhi")]),
    ]
    legs = [d["timeline"][0]["is_gateway_leg"] for d in _enrich(days)["days"]]
    assert legs == [True, False, True]


def test_gateway_legs_are_origin_agnostic_first_and_last_travel_leg():
    # TWM-215: origin unset, and neither leg touches any "origin" — the first
    # and last TRAVEL legs are still the gateway legs.
    days = [
        _day(1, [_travel("Sumerpur", "Jodhpur")]),
        _day(2, [_travel("Jodhpur", "Jaisalmer")]),
        _day(3, [_travel("Jaisalmer", "Sumerpur")]),
    ]
    legs = [d["timeline"][0]["is_gateway_leg"] for d in _enrich(days, trip_context={})["days"]]
    assert legs == [True, False, True]


def test_split_outbound_and_return_both_flagged_as_gateway():
    # Group meets at the destination and disperses afterward — the outbound
    # and return legs start/end at different cities; both still gateway.
    days = [
        _day(1, [_travel("Delhi", "Udaipur")]),
        _day(2, [_travel("Udaipur", "Mumbai")]),
    ]
    legs = [d["timeline"][0]["is_gateway_leg"] for d in _enrich(days, trip_context={"origin_city": "Chennai"})["days"]]
    assert legs == [True, True]


def test_single_travel_leg_trip_flags_its_one_leg():
    legs = [d["timeline"][0]["is_gateway_leg"] for d in _enrich([_day(1, [_travel("Delhi", "Jaipur")])])["days"]]
    assert legs == [True]


_HUBS = [
    {"city": "Udaipur", "side": "destination", "last_mile_km": 100,
     "last_mile_duration_minutes": 150, "long_haul_distance_km": 660, "access_gap": "air"},
    {"city": "Unmapped Rail Junction", "side": "destination", "last_mile_km": 60,
     "last_mile_duration_minutes": 90, "long_haul_distance_km": 300, "access_gap": "rail"},
]


def test_gateway_leg_hubs_are_replaced_with_per_mode_transport_options():
    days = [_day(1, [_travel("Bengaluru", "Sumerpur", hubs=_HUBS)])]
    item = _enrich(days)["days"][0]["timeline"][0]
    assert "hubs" not in item
    options = {option["mode"]: option for option in item["transport_options"]}
    assert options["flight"]["direct"] is False
    assert options["flight"]["feasible"] is True
    assert options["flight"]["ruled_out_reason"] is None
    assert options["flight"]["hubs"] == [{
        "city": "Udaipur",
        "access_gap": "air",
        "side": "destination",
        "last_mile_km": 100,
        "last_mile_duration_minutes": 150,
        "distance_km": 660,
        "long_haul_distance_km": 660,
        "feasible": True,
    }]
    assert options["train"]["direct"] is False
    assert options["train"]["hubs"][0]["city"] == "Unmapped Rail Junction"
    assert options["train"]["hubs"][0]["feasible"] is True
    assert options["bus"]["hubs"][0]["feasible"] is True
    assert options["drive"]["feasible"] is True
    assert options["drive"]["ruled_out_reason"] is None


def test_station_no_airport_leg_does_not_guess_train_direct_without_rail_gap():
    hubs = [
        {"city": "Udaipur", "side": "destination", "access_gap": "air", "last_mile_km": 100,
         "last_mile_duration_minutes": 150, "long_haul_distance_km": 660},
    ]
    days = [_day(1, [_travel("Bengaluru", "Sumerpur", hubs=hubs)])]
    options = {option["mode"]: option for option in _enrich(days)["days"][0]["timeline"][0]["transport_options"]}
    assert options["flight"]["direct"] is False
    assert options["train"]["direct"] is True
    assert options["train"]["feasible"] is False
    assert "No reliable distance" in options["train"]["ruled_out_reason"]
    assert options["bus"]["direct"] is True
    assert options["bus"]["feasible"] is False
    assert "No reliable distance" in options["bus"]["ruled_out_reason"]


def test_hubless_origin_leg_uses_hub_to_town_pair_and_side_origin():
    # Outbound leg *from* a hubless origin: the long-haul portion is
    # hub -> real destination, and side "origin" is honoured when the
    # resolver can place the real endpoint.
    hubs = [{"city": "Udaipur", "side": "origin", "access_gap": "air", "last_mile_km": 100,
             "last_mile_duration_minutes": 150, "long_haul_distance_km": 660}]
    days = [
        _day(1, [_travel("Sumerpur", "Mumbai", hubs=hubs)]),
        _day(2, [_travel("Mumbai", "Sumerpur")]),
    ]
    flight = _enrich(days)["days"][0]["timeline"][0]["transport_options"][0]
    assert flight["mode"] == "flight"
    assert flight["hubs"][0]["feasible"] is True  # Udaipur -> Mumbai is a real long hop


def test_hub_with_no_resolvable_endpoint_and_no_distance_stays_unresolved_and_warns():
    # Defensive path: a malformed hub with no distance ballpark and no
    # resolvable endpoint fails closed to no modes, and the leg is logged at
    # WARNING so the gap is visible in Axiom.
    hubs = [{"city": "Also Unmapped Town", "side": "destination", "access_gap": "air", "last_mile_km": 40,
             "last_mile_duration_minutes": 60}]
    logger, sink = _logger()
    days = [_day(1, [_travel("Unmapped Origin", "Unmapped Dest", hubs=hubs)])]
    hub_out = _enrich(days, logger=logger)["days"][0]["timeline"][0]["transport_options"][0]["hubs"][0]
    assert hub_out["feasible"] is False
    assert _enrich(days)["days"][0]["timeline"][0]["transport_options"][0]["feasible"] is False
    warn = [e for e in sink.events if e["event"] == "be.itinerary.hub_resolution" and e["level"] == "WARNING"]
    assert warn and warn[0]["fields"]["mode_resolution_counts"]["flight"]["feasible_hub_count"] == 0


def test_hub_endpoint_pairing_prefers_the_unresolvable_side_then_falls_back_to_atlas_side():
    dest_hub = {"city": "Udaipur", "side": "destination"}
    origin_hub = {"city": "Udaipur", "side": "origin"}

    # to_city hubless -> pair from_city -> hub, regardless of Atlas `side`
    assert _hub_long_haul_endpoints("Bengaluru", "Sumerpur", False, True, origin_hub) == ("Bengaluru", "Udaipur")
    # from_city hubless -> pair hub -> to_city
    assert _hub_long_haul_endpoints("Sumerpur", "Mumbai", True, False, dest_hub) == ("Udaipur", "Mumbai")
    # both resolvable (defensive) -> Atlas `side` decides
    assert _hub_long_haul_endpoints("Bengaluru", "Delhi", False, False, origin_hub) == ("Udaipur", "Delhi")
    assert _hub_long_haul_endpoints("Bengaluru", "Delhi", False, False, dest_hub) == ("Bengaluru", "Udaipur")


def test_non_gateway_leg_hubs_are_left_untouched():
    days = [
        _day(1, [_travel("Delhi", "Jaipur")]),
        _day(2, [_travel("Jaipur", "Sumerpur", hubs=_HUBS)]),
        _day(3, [_travel("Sumerpur", "Delhi")]),
    ]
    mid_leg = _enrich(days)["days"][1]["timeline"][0]
    assert "feasible_modes" not in mid_leg["hubs"][0]
    assert "transport_options" not in mid_leg


def test_gateway_leg_suppresses_hub_that_is_an_itinerary_city():
    hubs = [
        {"city": "Udaipur", "side": "destination", "access_gap": "air", "last_mile_km": 100,
         "last_mile_duration_minutes": 150, "long_haul_distance_km": 660},
        {"city": "Ahmedabad", "side": "destination", "access_gap": "air", "last_mile_km": 220,
         "last_mile_duration_minutes": 300, "long_haul_distance_km": 800},
    ]
    days = [
        _day(1, [_travel("Bengaluru", "Sumerpur", hubs=hubs)], primary="Sumerpur"),
        _day(2, [_travel("Sumerpur", "Ahmedabad")], primary="Ahmedabad"),
    ]
    options = _enrich(days)["days"][0]["timeline"][0]["transport_options"]
    flight = next(option for option in options if option["mode"] == "flight")
    assert [hub["city"] for hub in flight["hubs"]] == ["Udaipur"]


def test_gateway_leg_drops_mode_when_all_hubs_for_access_gap_are_suppressed():
    hubs = [
        {"city": "Ahmedabad", "side": "destination", "access_gap": "air", "last_mile_km": 220,
         "last_mile_duration_minutes": 300, "long_haul_distance_km": 800},
    ]
    days = [
        _day(1, [_travel("Bengaluru", "Sumerpur", hubs=hubs)], primary="Sumerpur"),
        _day(2, [_travel("Sumerpur", "Ahmedabad")], primary="Ahmedabad"),
    ]
    options = _enrich(days)["days"][0]["timeline"][0]["transport_options"]
    assert "flight" not in {option["mode"] for option in options}


def test_gateway_leg_adds_ruled_out_reasons_and_long_journey_note():
    hubs = [
        {"city": "Falna", "side": "destination", "access_gap": "rail", "last_mile_km": 15,
         "last_mile_duration_minutes": 25, "long_haul_distance_km": 1600},
    ]
    days = [_day(1, [_travel("Bhubaneswar", "Sumerpur", hubs=hubs)])]
    options = {option["mode"]: option for option in _enrich(days)["days"][0]["timeline"][0]["transport_options"]}
    assert options["flight"]["long_journey_note"] is None
    assert options["train"]["feasible"] is True
    assert options["train"]["direct"] is False
    assert options["train"]["long_journey_note"] == "Roughly 36 h long-haul journey before the local transfer."
    assert options["bus"]["feasible"] is False
    assert "Too far for a bus" in options["bus"]["ruled_out_reason"]
    assert options["drive"]["feasible"] is False
    assert options["drive"]["ruled_out_reason"] == "Too far for a single road trip (~1,600 km)."


def test_hub_resolution_emits_a_structured_event_with_trip_id():
    logger, sink = _logger()
    _enrich([_day(1, [_travel("Bengaluru", "Sumerpur", hubs=_HUBS)])], logger=logger)
    events = [e for e in sink.events if e["event"] == "be.itinerary.hub_resolution"]
    assert len(events) == 1
    fields = events[0]["fields"]
    assert fields["trip_id"] == str(TRIP_ID)
    assert fields["candidate_hub_count"] == 2
    assert fields["suppressed_hub_count"] == 0
    assert fields["mode_resolution_counts"]["flight"]["resolution"] == "via_hub"
    assert fields["mode_resolution_counts"]["train"]["feasible_hub_count"] == 1
    assert fields["mode_resolution_counts"]["drive"]["feasible_count"] == 1


def test_resolved_date_precedence_search_pref_then_trip_dates_then_none():
    days = [_day(1, [_travel("Delhi", "Jaipur")]), _day(2, [_travel("Jaipur", "Delhi")])]

    # none: no trip dates, no pref
    plain = _enrich(days)["days"][0]["timeline"][0]
    assert (plain["resolved_date"], plain["date_source"]) == (None, "none")

    # trip_dates: exact composed dates -> day K = departure + K-1
    dated = _enrich(days, travel_dates="2026-05-01")["days"]
    assert (dated[0]["timeline"][0]["resolved_date"], dated[0]["timeline"][0]["date_source"]) == ("2026-05-01", "trip_dates")
    assert dated[1]["timeline"][0]["resolved_date"] == "2026-05-02"

    # search_pref wins over trip_dates
    leg_id = f"{TRIP_ID}:1:0"
    override = _enrich(days, travel_dates="2026-05-01",
                       booking_setup={"search_prefs": {"transports": {leg_id: {"precision": "exact", "date": "2026-06-15"}}}})
    item = override["days"][0]["timeline"][0]
    assert (item["resolved_date"], item["date_source"]) == ("2026-06-15", "search_pref")


def test_month_precision_trip_dates_do_not_produce_a_per_day_calendar_date():
    days = [_day(1, [_travel("Delhi", "Jaipur")])]
    item = _enrich(days, travel_dates="May 2026")["days"][0]["timeline"][0]
    assert (item["resolved_date"], item["date_source"]) == (None, "none")


def test_stay_segments_group_consecutive_same_location_nights():
    days = [
        _day(1, [_travel("Delhi", "Jaipur"), _stay("Jaipur")]),
        _day(2, [_stay("Jaipur")]),
        _day(3, [_travel("Jaipur", "Agra"), _stay("Agra")]),
        _day(4, [_travel("Agra", "Delhi")]),
    ]
    segments = _enrich(days)["stay_segments"]
    assert [(s["location"], s["nights"]) for s in segments] == [("Jaipur", 2), ("Agra", 1)]


def test_stay_segment_checkin_follows_trip_dates_then_search_pref():
    days = [_day(1, [_stay("Jaipur")]), _day(2, [_stay("Jaipur")])]
    dated = _enrich(days, travel_dates="2026-05-01")["stay_segments"][0]
    assert dated["checkin_date"] == "2026-05-01" and dated["checkout_date"] == "2026-05-03"
    assert dated["date_source"] == "trip_dates"

    segment_id = f"{TRIP_ID}:stay:1:2:jaipur"
    override = _enrich(days, travel_dates="2026-05-01",
                       booking_setup={"search_prefs": {"stays": {segment_id: {"precision": "exact", "date": "2026-07-10"}}}})
    seg = override["stay_segments"][0]
    assert seg["checkin_date"] == "2026-07-10" and seg["date_source"] == "search_pref"


def test_stay_segment_checkout_override_beats_the_itinerary_night_count():
    days = [_day(1, [_stay("Jaipur")]), _day(2, [_stay("Jaipur")])]
    segment_id = f"{TRIP_ID}:stay:1:2:jaipur"

    # An explicit check-out wins over check-in + itinerary nights.
    moved = _enrich(days, travel_dates="2026-05-01", booking_setup={"search_prefs": {"stays": {
        segment_id: {"precision": "exact", "date": "2026-07-10", "checkout_date": "2026-07-15"}}}})
    seg = moved["stay_segments"][0]
    assert (seg["checkin_date"], seg["checkout_date"]) == ("2026-07-10", "2026-07-15")
    assert seg["nights"] == 2  # the plan's night count is untouched

    # No check-out override -> fall back to check-in + itinerary nights.
    kept = _enrich(days, travel_dates="2026-05-01", booking_setup={"search_prefs": {"stays": {
        segment_id: {"precision": "exact", "date": "2026-07-10"}}}})
    assert kept["stay_segments"][0]["checkout_date"] == "2026-07-12"


def test_no_feasible_modes_field_on_enriched_items():
    item = _enrich([_day(1, [_travel("Delhi", "Jaipur")])])["days"][0]["timeline"][0]
    assert "feasible_modes" not in item
