"""TWM-217: TripViewService — each _compose_* block in isolation."""

from uuid import uuid4

import pytest

from twm.services.trip_view import TripViewService

TRIP_ID = uuid4()
SERVICE = TripViewService()


def _final_itinerary(**overrides):
    base = {
        "trip_summary": {
            "title": "Kerala unwind", "destinations": ["Kochi", "Alleppey"],
            "trip_duration": 4, "num_travelers": 3,
            "overview": "A slow backwater week.", "route_rationale": "Coast then backwaters.",
        },
        "days": [{"day_number": n, "title": f"Day {n}", "primary_location": "Kochi",
                  "summary": "s", "timeline": [{"kind": "ACTIVITY", "title": "x", "location": "Kochi",
                                                "detail": "d", "reference": {"status": "GENERAL_GUIDANCE"}}],
                  "notes": []} for n in range(1, 5)],
        "budget_summary": {
            "currency": "INR",
            "lines": [{"category": "Stay", "amount_low": 20000, "amount_high": 40000, "note": "n"}],
            "total_low": 20000, "total_high": 62200,
        },
        "practical_notes": [],
        "sources": [],
        "assumptions": [],
    }
    base.update(overrides)
    return base


def _build(*, trip_state=None, itinerary=None, has_recommendation=False):
    return SERVICE.build(
        trip_id=TRIP_ID, title="T", product_mode="self_led", version=1,
        trip_state=trip_state or {}, ui_state={},
        itinerary_result=None if itinerary is None else {"final_itinerary": itinerary},
        has_recommendation=has_recommendation,
    )


# ---- resume blocks ----------------------------------------------------------

def test_lifecycle_reads_the_columns_and_selected_option():
    view = _build(trip_state={"stage": "matched", "status": "free", "active_agent": None,
                              "selected_option": {"type": "single", "id": "goa"}})
    assert view.lifecycle.stage == "matched"
    assert view.lifecycle.selected_option == {"type": "single", "id": "goa"}


@pytest.mark.parametrize("context,expected", [
    ({}, {}),
    ({"origin_city": "Delhi"}, {"origin_city": "Delhi"}),
    ({"origin_city": "Delhi", "budget": "INR 50000", "destinations": ["Goa"], "junk": "x"},
     {"origin_city": "Delhi", "budget": "INR 50000", "destinations": "Goa"}),
])
def test_context_recap_covers_only_addressable_keys(context, expected):
    view = _build(trip_state={"trip_context": context})
    assert {r.key: r.value for r in view.context_recap} == expected


def test_plan_is_none_before_guide_runs_and_passthrough_after():
    assert _build(trip_state={"planner_state": {}}).plan is None
    view = _build(trip_state={"planner_state": {
        "places": ["Fort Kochi"],
        "day_plan": [{"day_number": 1, "places": ["Fort Kochi"], "pace": "relaxed", "buffer_note": None}],
        "conversation_context": {"awaiting": None},
        "frozen_plan": {"guide_revision": 1},
    }})
    assert view.plan.places == ["Fort Kochi"]
    assert view.plan.day_plan[0].pace == "relaxed"
    assert view.plan.frozen is True


def test_plan_never_leaks_a_non_allowlisted_key():
    view = _build(trip_state={"planner_state": {"places": ["x"], "superseded_planner_states": [{"secret": 1}]}})
    assert not hasattr(view.plan, "superseded_planner_states")
    assert view.plan.model_dump().keys() == {"places", "day_plan", "frozen", "awaiting"}


def test_matcher_resume_signal():
    view = _build(
        trip_state={"matcher_state": {"conversation_context": {"last_meridian_message": "Tell me more.", "awaiting": "budget"}}},
        has_recommendation=True,
    )
    assert view.matcher.last_message == "Tell me more."
    assert view.matcher.awaiting == "budget"
    assert view.matcher.has_recommendation is True


# ---- itinerary-derived blocks --------------------------------------------

def test_the_five_composed_blocks_are_null_pre_itinerary():
    view = _build(trip_state={"trip_context": {"origin_city": "Delhi"}})
    assert view.summary is None
    assert view.booking is None
    assert view.budget_breakdown is None
    assert view.open_gaps is None
    assert view.before_you_go is None


def test_the_five_composed_blocks_are_populated_post_itinerary():
    view = _build(itinerary=_final_itinerary())
    assert view.summary.title == "Kerala unwind"
    assert view.booking.party is None
    assert view.budget_breakdown.lines[0].low == 20000  # rename of amount_low
    assert view.open_gaps == [] or view.open_gaps[0].resolution == "set_party"
    assert view.before_you_go == []


@pytest.mark.parametrize("trip_state,trip_summary,expected_value,expected_source", [
    ({"booking_setup": {"party": {"adults": 2, "children": 0, "infants": 1}}}, {}, "2 adults, 1 infant", "party"),
    ({}, {"num_travelers": 3}, "~3", "itinerary_estimate"),
    ({"trip_context": {"num_travelers": "about 4 of us"}}, {"num_travelers": None}, "~4", "conversational"),
    ({}, {"num_travelers": None}, None, "unknown"),
])
def test_summary_travelers_precedence(trip_state, trip_summary, expected_value, expected_source):
    view = _build(trip_state=trip_state, itinerary=_final_itinerary(
        trip_summary={**_final_itinerary()["trip_summary"], **trip_summary}))
    assert view.summary.travelers.value == expected_value
    assert view.summary.travelers.source == expected_source


def test_summary_dates_carry_the_source():
    view = _build(trip_state={"trip_context": {"travel_dates": "March 2026"}}, itinerary=_final_itinerary())
    assert view.summary.dates.precision == "month"
    assert view.summary.dates.source == "conversational"
    none_view = _build(itinerary=_final_itinerary())
    assert none_view.summary.dates.precision == "none"
    assert none_view.summary.dates.source == "none"


# ---- budget_breakdown.fit_note (composer, deterministic) -----------------

def test_fit_note_says_percent_over_when_over_a_currency_matched_ceiling():
    view = _build(trip_state={"trip_context": {"budget": "INR 50000"}}, itinerary=_final_itinerary())
    assert "~24% over" in view.budget_breakdown.fit_note
    assert "INR 50,000" in view.budget_breakdown.fit_note


def test_fit_note_says_within_when_under_budget():
    view = _build(trip_state={"trip_context": {"budget": "100000 INR"}}, itinerary=_final_itinerary())
    assert "Within your stated" in view.budget_breakdown.fit_note


def test_fit_note_states_the_range_only_when_currency_mismatches_or_unparseable():
    mismatch = _build(trip_state={"trip_context": {"budget": "USD 500"}}, itinerary=_final_itinerary())
    assert "over" not in mismatch.budget_breakdown.fit_note
    assert mismatch.budget_breakdown.fit_note == "Estimated INR 20,000–INR 62,200."
    vague = _build(trip_state={"trip_context": {"budget": "not much"}}, itinerary=_final_itinerary())
    assert vague.budget_breakdown.fit_note == "Estimated INR 20,000–INR 62,200."


def test_fit_note_flags_a_party_that_differs_from_the_estimate():
    view = _build(
        trip_state={"booking_setup": {"party": {"adults": 5, "children": 0, "infants": 0}}},
        itinerary=_final_itinerary(),  # num_travelers 3
    )
    assert view.budget_breakdown.party_changed_since is True
    assert "built for 3 travellers" in view.budget_breakdown.fit_note


def test_party_changed_since_is_false_when_the_itinerary_read_no_count():
    view = _build(
        trip_state={"booking_setup": {"party": {"adults": 2, "children": 0, "infants": 0}}},
        itinerary=_final_itinerary(trip_summary={
            **_final_itinerary()["trip_summary"], "num_travelers": None}),
    )
    assert view.budget_breakdown.estimated_for_travelers is None
    assert view.budget_breakdown.party_changed_since is False


# ---- open_gaps ----------------------------------------------------------

def test_open_gaps_is_one_set_party_gap_when_party_is_unset():
    view = _build(itinerary=_final_itinerary())
    assert [g.resolution for g in view.open_gaps] == ["set_party"]


def test_open_gaps_is_empty_when_party_is_set_and_never_a_date_gap():
    view = _build(
        trip_state={"booking_setup": {
            "party": {"adults": 2, "children": 0, "infants": 0},
            "search_prefs": {},
        }, "trip_context": {"travel_dates": "flexible"}},
        itinerary=_final_itinerary(),
    )
    assert view.open_gaps == []


# ---- before_you_go ----------------------------------------------------

def test_before_you_go_passes_through_notes_and_synthesises_assumption_titles():
    view = _build(itinerary=_final_itinerary(
        practical_notes=[{"category": "Safety", "title": "Carry cash", "detail": "ATMs are sparse.",
                          "reference": {"status": "GENERAL_GUIDANCE"}, "needs_verification": True}],
        assumptions=[{"category": "stay_area", "detail": "Assumed a Fort Kochi base."},
                     {"category": "other", "detail": "Boats stop by 6pm. Plan around it."}],
    ))
    titles = [(i.title, i.verify) for i in view.before_you_go]
    assert ("Carry cash", True) in titles
    assert ("Where you'll stay", True) in titles
    assert ("Boats stop by 6pm", True) in titles
