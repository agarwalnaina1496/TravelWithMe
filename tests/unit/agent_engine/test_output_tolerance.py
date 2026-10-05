"""Proactive slip catalogue for every agent's output contract (TWM-234).

Each case takes a valid recorded completion, applies one harmless slip an LLM
plausibly makes, and asserts the whole response survives on the *first*
attempt -- healed, with the heal named on the telemetry event -- instead of
costing a ~35s retry and then a traveler-visible failure.

A new validator on an agent output has to land either here (it heals) or in
the rejection list at the bottom (it protects a consumer or the trust
boundary, and the test says why).
"""

import asyncio
import glob
import json
from copy import deepcopy

import pytest

from tests.factories import recommendation_option, traveler_criteria
from tests.unit.agent_engine.test_service import service_with_outputs
from twm.services import AgentOutputError
from twm.telemetry import InMemorySink

FIXTURES = "tests/resources/harness_fixtures/{agent}__{case}__*.json"


def recorded(agent: str, case: str) -> dict:
    path = glob.glob(FIXTURES.format(agent=agent, case=case))[0]
    with open(path, encoding="utf8") as handle:
        return json.loads(json.loads(handle.read())["raw_output"])


def meridian_success() -> dict:
    return {
        "status": "SUCCESS",
        "message": "The first option is the strongest overall fit.",
        "state_delta": {"matcher_state": {"conversation_context": {"awaiting": None}}},
        "traveler_criteria": traveler_criteria(),
        "options": [recommendation_option(1), recommendation_option(2)],
    }


def meridian_clarification() -> dict:
    return {
        "status": "NEEDS_CLARIFICATION",
        "message": "Where will you be traveling from?",
        "state_delta": {
            "matcher_state": {
                "conversation_context": {
                    "awaiting": "origin_city",
                    "last_meridian_message": "Where will you be traveling from?",
                }
            }
        },
    }


def run(monkeypatch, agent: str, output: dict):
    sink = InMemorySink()
    engine, adapter = service_with_outputs(monkeypatch, json.dumps(output), telemetry_sink=sink)
    call = getattr(engine, agent)
    result = asyncio.run(call({}, "message"))
    healed = [
        name
        for event in sink.events
        if event["event"] == "be.agent.output.normalized"
        for name in event["fields"]["normalizations_applied"]
    ]
    return result.response, adapter.invoke.await_count, healed


def with_two_criteria_sharing_one_field(output: dict) -> dict:
    # The live failure: one free-text field ("mountains, less crowd, safety,
    # concerned about extreme cold") legitimately feeds several asks.
    second = {"id": "crowds", "label": "Few crowds", "requirement_type": "PREFERENCE"}
    for criterion in (*output["traveler_criteria"], second):
        criterion["source_context_paths"] = ["travel_preferences"]
    output["traveler_criteria"].append(second)
    for option in output["options"]:
        option["evaluations"].append(
            {
                "criterion_id": "crowds",
                "outcome": "MATCH",
                "conclusion": "Quiet outside peak season.",
                "details": [{"type": "bullets", "items": ["Few visitors."]}],
            }
        )
    return output


def drop_awaiting(output: dict) -> dict:
    output["state_delta"]["matcher_state"]["conversation_context"].pop("awaiting")
    return output


def stray_last_message(output: dict) -> dict:
    output["state_delta"]["matcher_state"]["conversation_context"]["last_meridian_message"] = "Something else."
    return output


def scrambled_ranks(output: dict) -> dict:
    output["options"][0]["rank"] = 2
    output["options"][1]["rank"] = 2
    return output


def four_options(output: dict) -> dict:
    output["options"] = [recommendation_option(rank) for rank in range(1, 5)]
    return output


def empty_lists_on_a_clarification(output: dict) -> dict:
    output["traveler_criteria"] = []
    output["options"] = []
    output["constraint_adjustment_suggestions"] = []
    return output


@pytest.mark.parametrize(
    ("base", "mutate", "check"),
    [
        (meridian_success, with_two_criteria_sharing_one_field,
         lambda r: "source_context_paths" not in r["traveler_criteria"][0]),
        (meridian_success, lambda o: o.update(trip_type="circuit") or o,
         lambda r: r["trip_type"] == "single"),
        (meridian_success, scrambled_ranks,
         lambda r: [o["rank"] for o in r["options"]] == [1, 2]),
        (meridian_success, four_options,
         lambda r: len(r["options"]) == 3),
        (meridian_success, lambda o: o.update(constraint_adjustment_suggestions=["Raise the budget."]) or o,
         lambda r: "constraint_adjustment_suggestions" not in r),
        (meridian_success, lambda o: o["options"][0].update(verdict="legacy") or o,
         lambda r: "verdict" not in r["options"][0]),
        (meridian_success, drop_awaiting,
         lambda r: r["state_delta"]["matcher_state"]["conversation_context"]["awaiting"] is None),
        (meridian_success, stray_last_message,
         lambda r: r["state_delta"]["matcher_state"]["conversation_context"]["last_meridian_message"] == r["message"]),
        (meridian_clarification, empty_lists_on_a_clarification,
         lambda r: "traveler_criteria" not in r and r["options"] == []),
        (meridian_clarification, stray_last_message,
         lambda r: r["state_delta"]["matcher_state"]["conversation_context"]["last_meridian_message"] == r["message"]),
    ],
)
def test_meridian_slips_heal_on_the_first_attempt(monkeypatch, base, mutate, check):
    response, attempts, _ = run(monkeypatch, "meridian", mutate(base()))

    assert attempts == 1
    assert check(response)


def guide_with_a_repeated_place(output: dict) -> dict:
    output["state_delta"]["planner_state"]["places"] = ["Ram Jhula", "ram jhula", "Triveni Ghat"]
    return output


def guide_with_awaiting_beside_its_container(output: dict) -> dict:
    planner = output["state_delta"]["planner_state"]
    planner.pop("conversation_context", None)
    planner["awaiting"] = "budget"
    return output


def test_guide_repeated_place_is_one_place(monkeypatch):
    response, attempts, healed = run(
        monkeypatch, "guide", guide_with_a_repeated_place(recorded("guide", "rishikesh-start"))
    )

    assert attempts == 1
    assert response["state_delta"]["planner_state"]["places"] == ["Ram Jhula", "Triveni Ghat"]
    assert "places.deduplicated" in healed


def test_guide_awaiting_beside_its_container_is_moved_in(monkeypatch):
    response, attempts, healed = run(
        monkeypatch, "guide", guide_with_awaiting_beside_its_container(recorded("guide", "rishikesh-start"))
    )

    assert attempts == 1
    assert response["state_delta"]["planner_state"]["conversation_context"]["awaiting"] == "budget"
    assert "awaiting.relocated_into_conversation_context" in healed


def atlas() -> dict:
    return recorded("atlas", sorted({p.split("__")[1] for p in glob.glob(FIXTURES.format(agent="atlas", case="*"))})[0])


def first_item(output: dict) -> dict:
    return output["final_itinerary"]["days"][0]["timeline"][0]


def unsourced_verified(output: dict) -> dict:
    first_item(output)["reference"] = {"status": "VERIFIED"}
    return output


def empty_stay_estimate(output: dict) -> dict:
    output["final_itinerary"]["days"][0]["stay_price_estimate"] = []
    return output


def unordered_stay_estimate(output: dict) -> dict:
    tier = lambda name, low, high: {"tier": name, "estimated_cost_low": low, "estimated_cost_high": high}  # noqa: E731
    output["final_itinerary"]["days"][0]["stay_price_estimate"] = [
        tier("premium", 3000, 5000), tier("budget", 500, 900), tier("mid_range", 1500, 2500),
    ]
    return output


def wrong_duration_echo(output: dict) -> dict:
    output["final_itinerary"]["trip_summary"]["trip_duration"] += 3
    return output


def misnumbered_days(output: dict) -> dict:
    for day in output["final_itinerary"]["days"]:
        day["day_number"] += 4
    return output


def readiness_without_the_flag(output: dict) -> dict:
    first_item(output).update(requires_advance_booking=False, booking_readiness="suggested")
    return output


def flag_without_readiness(output: dict) -> dict:
    first_item(output).update(requires_advance_booking=True, booking_readiness=None)
    return output


def lone_cost_bound(output: dict) -> dict:
    first_item(output).update(estimated_cost_low=500, estimated_cost_high=None)
    return output


def assumptions_inside_summary(output: dict) -> dict:
    output["final_itinerary"]["trip_summary"]["assumptions"] = [{"category": "other", "detail": "Mid-range stay."}]
    return output


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (unsourced_verified, lambda r: first_item(r)["reference"]["status"] == "GENERAL_GUIDANCE"),
        (empty_stay_estimate, lambda r: "stay_price_estimate" not in r["final_itinerary"]["days"][0]),
        (unordered_stay_estimate,
         lambda r: [t["tier"] for t in r["final_itinerary"]["days"][0]["stay_price_estimate"]] == ["budget", "mid_range", "premium"]),
        (wrong_duration_echo,
         lambda r: r["final_itinerary"]["trip_summary"]["trip_duration"] == len(r["final_itinerary"]["days"])),
        (misnumbered_days,
         lambda r: [d["day_number"] for d in r["final_itinerary"]["days"]] == list(range(1, len(r["final_itinerary"]["days"]) + 1))),
        (assumptions_inside_summary,
         lambda r: {"category": "other", "detail": "Mid-range stay."} in r["final_itinerary"]["assumptions"]
         and "assumptions" not in r["final_itinerary"]["trip_summary"]),
        (lambda o: first_item(o).update(note="extra") or o, lambda r: "note" not in first_item(r)),
        (readiness_without_the_flag,
         lambda r: first_item(r)["booking_readiness"] == "suggested" and first_item(r)["requires_advance_booking"] is True),
        (flag_without_readiness,
         lambda r: first_item(r)["booking_readiness"] == "needs_advance_booking" and first_item(r)["requires_advance_booking"] is True),
        (lone_cost_bound,
         lambda r: "estimated_cost_low" not in first_item(r) and "estimated_cost_high" not in first_item(r)),
    ],
)
def test_atlas_slips_heal_on_the_first_attempt(monkeypatch, mutate, check):
    response, attempts, _ = run(monkeypatch, "atlas", mutate(atlas()))

    assert attempts == 1
    assert check(response)


def test_scout_unknown_top_level_key_is_dropped(monkeypatch):
    response, attempts, _ = run(
        monkeypatch, "scout", {"message": "Hello", "intent": "advise", "reasoning": "private chain of thought"}
    )

    assert attempts == 1
    assert "reasoning" not in response


# What stays rejected, and why. Each of these protects a consumer or the trust
# boundary, so a retry (and, failing that, an error) is the right outcome.
REJECTED = [
    ("a success with no criteria/options -- nothing to show",
     lambda: {**meridian_success(), "traveler_criteria": None, "options": []}),
    ("an option that skips a criterion -- the UI compares options criterion by criterion",
     lambda: {**meridian_success(), "options": [{**recommendation_option(1), "evaluations": []}]}),
    ("an agent writing Backend-owned state -- the write boundary (and an injection signal)",
     lambda: {**meridian_success(), "state_delta": {"matcher_state": {"stage": "planned"}}}),
    ("a clarification that awaits nothing -- the traveler has nothing to answer",
     lambda: {**meridian_clarification(), "state_delta": {"matcher_state": {"conversation_context": {}}}}),
]


@pytest.mark.parametrize(("why", "build"), REJECTED, ids=[why for why, _ in REJECTED])
def test_contract_violations_that_protect_a_consumer_stay_rejected(monkeypatch, why, build):
    output = json.dumps(deepcopy(build()))
    engine, adapter = service_with_outputs(monkeypatch, output, output)

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.meridian({}, "message"))

    assert adapter.invoke.await_count == 2


def test_a_failed_attempt_is_retried_with_the_rules_it_broke(monkeypatch):
    broken = json.dumps({"status": "SUCCESS", "message": "No options.", "state_delta": {}, "options": []})
    good = json.dumps(meridian_success())
    engine, adapter = service_with_outputs(monkeypatch, broken, good)

    result = asyncio.run(engine.meridian({}, "message"))

    first, second = (call.args[1].system_prompt for call in adapter.invoke.await_args_list)
    assert result.response["status"] == "SUCCESS"
    assert "CORRECTION REQUIRED" not in first
    assert "CORRECTION REQUIRED" in second
    assert "SUCCESS requires traveler criteria" in second


def test_the_correction_never_echoes_model_controlled_text(monkeypatch):
    # An unknown key inside a strict branch fails validation; its (model
    # chosen) name must not travel back into the prompt.
    sneaky = json.dumps({"message": "Hi", "intent": "advise", "state_delta": {"ignore_previous_instructions": "x"}})
    engine, adapter = service_with_outputs(monkeypatch, sneaky, sneaky)

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.scout({}, "message"))

    second = adapter.invoke.await_args_list[1].args[1].system_prompt
    assert "CORRECTION REQUIRED" in second
    assert "ignore_previous_instructions" not in second
