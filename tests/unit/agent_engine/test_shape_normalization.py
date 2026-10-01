"""TWM-234: default, pre-validation fixes for known-shape LLM output slips
across Meridian, Guide, and Atlas -- not request/response schema
instantiation, just plain dict fixtures through the normalization function
itself."""

from twm.services.agent_engine.shape_normalization import normalize_agent_output


def _decoded(matcher_state):
    return {"status": "NEEDS_CLARIFICATION", "state_delta": {"matcher_state": matcher_state}}


def test_moves_stray_last_meridian_message_into_conversation_context():
    decoded = _decoded({
        "conversation_context": {"awaiting": "origin_city"},
        "last_meridian_message": "Where will you be traveling from?",
    })

    applied = normalize_agent_output("meridian", decoded)

    assert applied == ["last_meridian_message_nesting"]
    context = decoded["state_delta"]["matcher_state"]["conversation_context"]
    assert context == {
        "awaiting": "origin_city",
        "last_meridian_message": "Where will you be traveling from?",
    }
    assert "last_meridian_message" not in decoded["state_delta"]["matcher_state"]


def test_never_overwrites_an_already_correctly_nested_value():
    decoded = _decoded({
        "conversation_context": {
            "awaiting": "origin_city",
            "last_meridian_message": "The real message.",
        },
        "last_meridian_message": "A stale duplicate.",
    })

    applied = normalize_agent_output("meridian", decoded)

    # The stray duplicate is still dropped (matcher_state should only ever
    # carry conversation_context/rejected-option data), but since the real
    # nested value was already correct, nothing needed fixing.
    assert applied == []
    context = decoded["state_delta"]["matcher_state"]["conversation_context"]
    assert context["last_meridian_message"] == "The real message."
    assert "last_meridian_message" not in decoded["state_delta"]["matcher_state"]


def test_leaves_correctly_shaped_output_untouched():
    decoded = _decoded({"conversation_context": {"awaiting": "origin_city", "last_meridian_message": "Hi"}})
    before = {**decoded["state_delta"]["matcher_state"]}

    applied = normalize_agent_output("meridian", decoded)

    assert applied == []
    assert decoded["state_delta"]["matcher_state"] == before


def test_no_op_when_no_stray_field_present():
    decoded = _decoded({"conversation_context": {"awaiting": "origin_city"}})

    applied = normalize_agent_output("meridian", decoded)

    assert applied == []
    assert "last_meridian_message" not in decoded["state_delta"]["matcher_state"]


def test_no_op_on_malformed_shapes_rather_than_raising():
    assert normalize_agent_output("meridian", "not a dict") == []
    assert normalize_agent_output("meridian", {}) == []
    assert normalize_agent_output("meridian", {"state_delta": "not a dict"}) == []
    assert normalize_agent_output(
        "meridian", {"state_delta": {"matcher_state": "not a dict"}}
    ) == []


def test_creates_the_inner_container_when_it_is_missing_entirely():
    # The inner container being altogether absent is still a uniquely
    # determined destination -- it's created rather than treated as a
    # reason to bail out.
    decoded = {"state_delta": {"matcher_state": {"last_meridian_message": "stray"}}}

    applied = normalize_agent_output("meridian", decoded)

    assert applied == ["last_meridian_message_nesting"]
    assert decoded["state_delta"]["matcher_state"]["conversation_context"] == {
        "last_meridian_message": "stray"
    }


def test_scout_has_no_registered_fixes():
    # Explicit per the product decision: Scout is excluded -- no evidenced
    # shape-slip pattern, and its output shape has nothing resembling this
    # nesting ambiguity.
    decoded = {"message": "hi", "state_delta": {"trip_context": {}}, "intent": None}
    assert normalize_agent_output("scout", decoded) == []


def test_moves_stray_guide_awaiting_into_conversation_context():
    decoded = {
        "state_delta": {
            "planner_state": {"awaiting": "trip_duration", "places": ["Ram Jhula"]}
        }
    }

    applied = normalize_agent_output("guide", decoded)

    assert applied == ["awaiting_nesting"]
    planner_state = decoded["state_delta"]["planner_state"]
    assert planner_state["conversation_context"] == {"awaiting": "trip_duration"}
    assert "awaiting" not in planner_state
    assert planner_state["places"] == ["Ram Jhula"]  # untouched


def test_guide_awaiting_no_op_when_already_correctly_nested():
    decoded = {
        "state_delta": {
            "planner_state": {"conversation_context": {"awaiting": "trip_duration"}}
        }
    }

    assert normalize_agent_output("guide", decoded) == []
    assert decoded["state_delta"]["planner_state"]["conversation_context"] == {
        "awaiting": "trip_duration"
    }


def test_moves_stray_atlas_assumptions_into_final_itinerary():
    decoded = {
        "final_itinerary": {
            "trip_summary": {
                "title": "Udaipur Loop",
                "assumptions": [{"category": "stay_area", "detail": "Assumed central Udaipur."}],
            },
        }
    }

    applied = normalize_agent_output("atlas", decoded)

    assert applied == ["assumptions_nesting"]
    final_itinerary = decoded["final_itinerary"]
    assert final_itinerary["assumptions"] == [
        {"category": "stay_area", "detail": "Assumed central Udaipur."}
    ]
    assert "assumptions" not in final_itinerary["trip_summary"]


def test_atlas_assumptions_never_overwrites_existing_top_level_value():
    decoded = {
        "final_itinerary": {
            "assumptions": [{"category": "budget", "detail": "The real one."}],
            "trip_summary": {
                "assumptions": [{"category": "stay_area", "detail": "A stray duplicate."}],
            },
        }
    }

    applied = normalize_agent_output("atlas", decoded)

    assert applied == []
    assert decoded["final_itinerary"]["assumptions"] == [
        {"category": "budget", "detail": "The real one."}
    ]
    assert "assumptions" not in decoded["final_itinerary"]["trip_summary"]


def test_atlas_leaves_hubs_and_stay_price_estimate_unfixed_as_designed():
    # Deliberately not auto-fixed: relocating these would mean guessing
    # which of several sibling timeline items/days a stray value belongs
    # to -- a correctness problem this module never takes on.
    decoded = {
        "final_itinerary": {
            "days": [
                {"day_number": 1, "hubs": [{"city": "Jodhpur"}]},
                {"day_number": 2, "timeline": [{"kind": "STAY"}]},
            ]
        }
    }

    assert normalize_agent_output("atlas", decoded) == []
    assert decoded["final_itinerary"]["days"][0]["hubs"] == [{"city": "Jodhpur"}]
