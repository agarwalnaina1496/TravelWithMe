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
from twm.services.agent_engine import AgentExecutionService, OutputRetryPolicy
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


# --- type-level slips: casing, blank-for-null, null-for-list, formatted numbers

def success_with(mutate):
    output = meridian_success()
    mutate(output)
    return output


def cost_detail(**fields) -> dict:
    return {"type": "cost_breakdown", "currency": "INR", **fields}


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda o: o.update(status="success"), lambda r: r["status"] == "SUCCESS"),
        (lambda o: o["traveler_criteria"][0].update(requirement_type="preference"),
         lambda r: r["traveler_criteria"][0]["requirement_type"] == "PREFERENCE"),
        (lambda o: o["options"][0]["evaluations"][0].update(outcome="Match"),
         lambda r: r["options"][0]["evaluations"][0]["outcome"] == "MATCH"),
        (lambda o: o["options"][0]["evaluations"][0].update(
            details=[cost_detail(currency="inr", per_person_total={"minimum": "1,500", "maximum": "₹2,000"})]),
         lambda r: r["options"][0]["evaluations"][0]["details"][0]["per_person_total"] == {"minimum": 1500.0, "maximum": 2000.0}
         and r["options"][0]["evaluations"][0]["details"][0]["currency"] == "INR"),
        (lambda o: o["options"][0].update(other_considerations=None, circuit_id=""),
         lambda r: "circuit_id" not in r["options"][0]),
        (lambda o: o["options"][0]["evaluations"][0].update(tradeoffs=None),
         lambda r: r["options"][0]["evaluations"][0]["tradeoffs"] == []),
        (lambda o: o["options"][0]["evaluations"][0].update(details=[cost_detail(note=" ", group_total={"minimum": 1, "maximum": 2})]),
         lambda r: "note" not in r["options"][0]["evaluations"][0]["details"][0]),
        (lambda o: o.update(state_delta=None), lambda r: r["status"] == "SUCCESS"),
        (lambda o: o["state_delta"]["matcher_state"].update(generated_title="  A   very\nspaced   title "),
         lambda r: r["state_delta"]["matcher_state"]["generated_title"] == "A very spaced title"),
        (lambda o: o["state_delta"]["matcher_state"].update(generated_title="word " * 60),
         lambda r: 0 < len(r["state_delta"]["matcher_state"]["generated_title"]) <= 120),
        (lambda o: o["state_delta"]["matcher_state"].update(generated_title={"not": "text"}),
         lambda r: "generated_title" not in r["state_delta"]["matcher_state"]),
    ],
)
def test_meridian_type_slips_heal_on_the_first_attempt(monkeypatch, mutate, check):
    response, attempts, _ = run(monkeypatch, "meridian", success_with(mutate))

    assert attempts == 1
    assert check(response)


def guide_plan() -> dict:
    return recorded("guide", "anything-else-answered-generates-plan")


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda o: o["state_delta"]["planner_state"]["day_plan"][0].update(pace="Relaxed"),
         lambda r: r["state_delta"]["planner_state"]["day_plan"][0]["pace"] == "relaxed"),
        (lambda o: o["state_delta"]["planner_state"]["day_plan"][0].update(buffer_note="", date=""),
         lambda r: "buffer_note" not in r["state_delta"]["planner_state"]["day_plan"][0]),
        (lambda o: o["state_delta"]["planner_state"]["day_plan"][0].update(places=None),
         lambda r: r["state_delta"]["planner_state"]["day_plan"][0]["places"] == []),
        (lambda o: o["state_delta"]["planner_state"].update(
            day_plan=[{**day, "day_number": day["day_number"] + 3} for day in o["state_delta"]["planner_state"]["day_plan"]]),
         lambda r: [d["day_number"] for d in r["state_delta"]["planner_state"]["day_plan"]]
         == list(range(1, len(r["state_delta"]["planner_state"]["day_plan"]) + 1))),
        (lambda o: o["state_delta"]["planner_state"].update(generated_title="x" * 400),
         lambda r: len(r["state_delta"]["planner_state"].get("generated_title", "")) <= 120),
        (lambda o: o["state_delta"]["planner_state"].update(generated_title="   "),
         lambda r: "generated_title" not in r["state_delta"]["planner_state"]),
        (lambda o: o.update(outcome="Continue"), lambda r: r["outcome"] == "continue"),
        (lambda o: o.update(state_delta=None), lambda r: "message" in r),
    ],
)
def test_guide_type_slips_heal_on_the_first_attempt(monkeypatch, mutate, check):
    output = guide_plan()
    mutate(output)
    response, attempts, _ = run(monkeypatch, "guide", output)

    assert attempts == 1
    assert check(response)


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda o: first_item(o).update(kind="activity", reference={"status": "General_Guidance"}),
         lambda r: first_item(r)["kind"] == "ACTIVITY"),
        (lambda o: first_item(o).update(backup_plan="", movement_guidance="", start_time=" "),
         lambda r: "movement_guidance" not in first_item(r) and "start_time" not in first_item(r)),
        (lambda o: o["final_itinerary"]["days"][0].update(notes=None, backup_plan=""),
         lambda r: r["final_itinerary"]["days"][0]["notes"] == []),
        (lambda o: o["final_itinerary"].update(assumptions=None, sources=None, practical_notes=None),
         lambda r: r["final_itinerary"]["assumptions"] == [] and r["final_itinerary"]["sources"] == []),
        (lambda o: first_item(o).update(estimated_cost_low=1250.5, estimated_cost_high="1,800"),
         lambda r: first_item(r)["estimated_cost_low"] == 1250 or first_item(r)["estimated_cost_low"] == 1251),
        (lambda o: o["final_itinerary"]["trip_summary"].update(num_travelers="2 adults, 1 child"),
         lambda r: "num_travelers" not in r["final_itinerary"]["trip_summary"]),
    ],
)
def test_atlas_type_slips_heal_on_the_first_attempt(monkeypatch, mutate, check):
    output = atlas()
    mutate(output)
    response, attempts, _ = run(monkeypatch, "atlas", output)

    assert attempts == 1
    assert check(response)


def test_scout_casing_and_null_state_delta_heal(monkeypatch):
    response, attempts, _ = run(
        monkeypatch, "scout", {"message": "Hello", "intent": "Advise", "state_delta": None}
    )

    assert attempts == 1
    assert response["intent"] == "advise"


def test_a_plan_the_business_rules_reject_is_retried_with_the_rule_stated(monkeypatch):
    # Backend rules the schema cannot know (they depend on the trip) are judged
    # before the response is accepted, so the model gets to correct them.
    first, second = json.dumps({"message": "First try", "state_delta": {}}), json.dumps(
        {"message": "Second try", "state_delta": {}}
    )
    engine, adapter = service_with_outputs(monkeypatch, first, second)
    verdicts = iter([["Each place must be allocated exactly once."], []])

    result = asyncio.run(engine.guide({}, "message", review=lambda response: next(verdicts)))

    sent = [call.args[1].system_prompt for call in adapter.invoke.await_args_list]
    assert result.response["message"] == "Second try"
    assert "CORRECTION REQUIRED" in sent[1]
    assert "Each place must be allocated exactly once." in sent[1]


def test_a_plan_that_keeps_breaking_the_rules_fails_after_the_retry(monkeypatch):
    output = json.dumps({"message": "Plan", "state_delta": {}})
    engine, adapter = service_with_outputs(monkeypatch, output, output)

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.guide({}, "message", review=lambda response: ["Day plan length must equal trip_duration."]))

    assert adapter.invoke.await_count == 2


GUIDE_REJECTED = [
    ("an invented `awaiting` slug -- the UI drives quick replies from the fixed set",
     lambda: {"message": "Q?", "state_delta": {"planner_state": {"conversation_context": {"awaiting": "travel_style"}}}}),
    ("a day without a pace -- the plan has nothing to show for it and Backend will not guess one",
     lambda: {"message": "Plan", "state_delta": {"planner_state": {"day_plan": [{"day_number": 1, "places": ["A"]}]}}}),
    ("a blank required message -- the traveler would see nothing",
     lambda: {"message": "  ", "state_delta": {}}),
]


@pytest.mark.parametrize(("why", "build"), GUIDE_REJECTED, ids=[why for why, _ in GUIDE_REJECTED])
def test_guide_violations_that_protect_a_consumer_stay_rejected(monkeypatch, why, build):
    output = json.dumps(build())
    engine, adapter = service_with_outputs(monkeypatch, output, output)

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.guide({}, "message"))

    assert adapter.invoke.await_count == 2


def test_an_itinerary_with_the_wrong_number_of_days_is_retried_with_the_rule_stated(monkeypatch):
    from twm.schemas.atlas import AtlasWorkingPlan
    from twm.services.trip_commands.atlas_commands import atlas_review

    base = atlas()
    expected = len(base["final_itinerary"]["days"])
    plan = AtlasWorkingPlan.model_validate(
        {
            "destinations": ["Goa"],
            "trip_duration": expected,
            "days": [{"day_number": number, "places": []} for number in range(1, expected + 1)],
        }
    )
    longer = deepcopy(base)
    longer["final_itinerary"]["days"].append(deepcopy(base["final_itinerary"]["days"][0]))
    engine, adapter = service_with_outputs(monkeypatch, json.dumps(longer), json.dumps(base))

    result = asyncio.run(engine.atlas({}, None, review=atlas_review(plan)))

    sent = [call.args[1].system_prompt for call in adapter.invoke.await_args_list]
    assert len(result.response["final_itinerary"]["days"]) == expected
    assert f"exactly {expected} days" in sent[1]


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda o: first_item(o).update(estimated_cost_low=2000, estimated_cost_high=800),
         lambda r: (first_item(r)["estimated_cost_low"], first_item(r)["estimated_cost_high"]) == (800, 2000)),
        (lambda o: o["final_itinerary"]["budget_summary"]["lines"][0].update(amount_low=900, amount_high=300),
         lambda r: r["final_itinerary"]["budget_summary"]["lines"][0]["amount_low"] == 300),
    ],
)
def test_atlas_inverted_ranges_are_put_the_right_way_round(monkeypatch, mutate, check):
    output = atlas()
    mutate(output)
    response, attempts, healed = run(monkeypatch, "atlas", output)

    assert attempts == 1
    assert check(response)
    assert any(name.endswith("range_swapped") for name in healed)


# --- retry policy -------------------------------------------------------------


def engine_with(monkeypatch, outputs, retry=None, clock=None):
    engine, adapter = service_with_outputs(monkeypatch, *outputs)
    engine = AgentExecutionService(adapter, engine._logger, "test-engine", retry=retry)
    return engine, adapter


def test_a_failing_response_gets_exactly_one_corrective_retry(monkeypatch):
    broken = json.dumps({"status": "SUCCESS", "message": "x", "state_delta": {}, "options": []})
    engine, adapter = engine_with(monkeypatch, [broken, broken])

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.meridian({}, "message"))

    assert adapter.invoke.await_count == 2


def test_a_new_attempt_is_not_started_when_it_would_overrun_the_time_budget(monkeypatch):
    broken = json.dumps({"status": "SUCCESS", "message": "x", "state_delta": {}, "options": []})
    # Any attempt takes longer than the budget allows another of, so one try only.
    engine, adapter = engine_with(
        monkeypatch, [broken, broken], retry=OutputRetryPolicy(budget_seconds=0.0)
    )

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.meridian({}, "message"))

    assert adapter.invoke.await_count == 1



def test_a_healed_response_emits_a_queryable_event_with_provenance(monkeypatch):
    sink = InMemorySink()
    engine, _ = service_with_outputs(
        monkeypatch, json.dumps(scrambled_ranks(meridian_success())), telemetry_sink=sink
    )

    asyncio.run(engine.meridian({}, "message"))

    event = next(e for e in sink.events if e["event"] == "be.agent.output.normalized")
    assert event["fields"]["agent"] == "meridian"
    assert event["fields"]["prompt_version"] == "test-version"
    assert event["fields"]["normalizations_applied"] == ["options.ranked_by_position"]
    assert event["fields"]["normalization_count"] == 1


# --- syntax slips: read, not retried ------------------------------------------

SCOUT_REPLY = '{"message": "Hello there", "intent": "advise"}'


@pytest.mark.parametrize(
    "raw",
    [
        '{"message": "Hello there", "intent": "advise",}',
        '{"message": "Hello there", "intent": "advise", "state_delta": None}',
        '{"message": "Hello there", // the reply\n "intent": "advise"}',
        '{"message": "Hello there", /* note */ "intent": "advise"}',
        '{"message": "Hello\nthere", "intent": "advise"}',
        'Sure! Here is the JSON:\n```json\n{"message": "Hello there", "intent": "advise",}\n```\nHope that helps.',
        '{"message": "Hello there", "intent": "advise", "state_delta": {"trip_context": {"flag": True, "gone": False,},},}',
    ],
    ids=["trailing-comma", "python-none", "line-comment", "block-comment", "raw-newline", "fenced-with-prose", "nested-python-literals"],
)
def test_a_syntax_slip_is_read_without_a_retry(monkeypatch, raw):
    engine, adapter = service_with_outputs(monkeypatch, raw)

    result = asyncio.run(engine.scout({}, "message"))

    assert adapter.invoke.await_count == 1
    assert result.response["message"].replace("\n", " ") == "Hello there"


def test_repairs_never_touch_text_inside_a_string(monkeypatch):
    tricky = '{"message": "None of these, True story, // not a comment, /* nor this */, trailing,]", "intent": "advise",}'
    engine, _ = service_with_outputs(monkeypatch, tricky)

    result = asyncio.run(engine.scout({}, "message"))

    assert result.response["message"] == "None of these, True story, // not a comment, /* nor this */, trailing,]"


@pytest.mark.parametrize(
    "raw",
    ['{"message": "cut off", "intent": "adv', '{"message": \'single quoted\'}'],
    ids=["truncated", "single-quoted"],
)
def test_output_that_changes_meaning_is_still_retried(monkeypatch, raw):
    engine, adapter = service_with_outputs(monkeypatch, raw, SCOUT_REPLY)

    result = asyncio.run(engine.scout({}, "message"))

    assert adapter.invoke.await_count == 2
    assert result.response["message"] == "Hello there"


def test_days_listed_out_of_order_keep_the_sequence_the_model_numbered_them(monkeypatch):
    output = guide_plan()
    days = output["state_delta"]["planner_state"]["day_plan"]
    assert len(days) >= 2
    days.reverse()  # listed last-day-first, numbered correctly
    expected = [day["places"] for day in sorted(days, key=lambda day: day["day_number"])]

    response, attempts, _ = run(monkeypatch, "guide", output)

    healed_days = response["state_delta"]["planner_state"]["day_plan"]
    assert attempts == 1
    assert [day["day_number"] for day in healed_days] == list(range(1, len(days) + 1))
    assert [day["places"] for day in healed_days] == expected


def test_the_retry_names_a_syntax_failure_plainly(monkeypatch):
    engine, adapter = service_with_outputs(monkeypatch, '{"message": "cut off', SCOUT_REPLY)

    asyncio.run(engine.scout({}, "message"))

    assert "not one complete, valid JSON object" in adapter.invoke.await_args_list[1].args[1].system_prompt


def test_a_stringly_typed_advance_booking_flag_is_still_read(monkeypatch):
    output = atlas()
    first_item(output).update(requires_advance_booking="true", booking_readiness=None)

    response, attempts, _ = run(monkeypatch, "atlas", output)

    assert attempts == 1
    assert first_item(response)["booking_readiness"] == "needs_advance_booking"


def test_a_crashing_validator_is_a_failed_attempt_not_a_server_error(monkeypatch):
    from twm.schemas import ScoutAgentOutput

    def explode(*args, **kwargs):
        raise RuntimeError("a healer had a bug")

    monkeypatch.setattr(ScoutAgentOutput, "model_validate", explode)
    sink = InMemorySink()
    engine, adapter = service_with_outputs(monkeypatch, SCOUT_REPLY, SCOUT_REPLY, telemetry_sink=sink)

    with pytest.raises(AgentOutputError) as captured:
        asyncio.run(engine.scout({}, "message"))

    assert adapter.invoke.await_count == 2
    assert captured.value.failures[0]["type"] == "validator_error"


def test_places_differing_only_by_whitespace_and_case_are_one_place(monkeypatch):
    output = guide_with_a_repeated_place(recorded("guide", "rishikesh-start"))
    output["state_delta"]["planner_state"]["places"] = ["Ram Jhula ", "ram jhula", "Triveni Ghat"]

    response, attempts, _ = run(monkeypatch, "guide", output)

    assert attempts == 1
    assert response["state_delta"]["planner_state"]["places"] == ["Ram Jhula", "Triveni Ghat"]
