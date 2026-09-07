"""TWM-217: the enriched /itinerary — item identity, resolved dates, stay
segments. The former TripBoardService logic, relocated (minus feasibility)."""

from uuid import uuid4

from twm.services.trip_view.itinerary_enrichment import enrich_itinerary
from twm.services.trip_view.trip_dates import compose_trip_dates

TRIP_ID = uuid4()


def _travel(from_city, to_city):
    return {"kind": "TRAVEL", "title": f"{from_city} to {to_city}", "location": f"{from_city} to {to_city}",
            "detail": "d", "from_city": from_city, "to_city": to_city, "reference": {"status": "GENERAL_GUIDANCE"}}


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


def _enrich(days, *, trip_context=None, booking_setup=None, travel_dates=None):
    ctx = trip_context or {"origin_city": "Delhi"}
    if travel_dates:
        ctx = {**ctx, "travel_dates": travel_dates}
    return enrich_itinerary(
        TRIP_ID, _itinerary(days), ctx, booking_setup or {},
        compose_trip_dates(ctx, len(days)),
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


def test_no_feasible_modes_field_on_enriched_items():
    item = _enrich([_day(1, [_travel("Delhi", "Jaipur")])])["days"][0]["timeline"][0]
    assert "feasible_modes" not in item
