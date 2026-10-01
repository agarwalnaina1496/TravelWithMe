"""TWM-234: narrowly-scoped, pre-validation fixes for known LLM output
shape slips -- not request/response schema instantiation, just plain dict
fixtures through the normalization function itself."""

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
    assert normalize_agent_output(
        "meridian",
        {"state_delta": {"matcher_state": {"last_meridian_message": "stray"}}},
    ) == []  # no conversation_context dict to move it into


def test_scout_has_no_registered_fixes():
    # Explicit per the product decision: Scout is excluded -- no evidenced
    # shape-slip pattern, and its output shape has nothing resembling this
    # nesting ambiguity.
    decoded = {"message": "hi", "state_delta": {"trip_context": {}}, "intent": None}
    assert normalize_agent_output("scout", decoded) == []


def test_guide_and_atlas_have_no_registered_fixes_yet():
    # No evidenced failure pattern for either yet -- a fix is added only
    # once a real one is observed, per this module's own convention.
    assert normalize_agent_output("guide", {"anything": "goes"}) == []
    assert normalize_agent_output("atlas", {"anything": "goes"}) == []
