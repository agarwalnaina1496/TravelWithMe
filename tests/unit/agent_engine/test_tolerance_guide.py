"""Guide slips heal on the first attempt; plan rules are reviewed before acceptance (TWM-234)."""

import asyncio
import json

import pytest

from tests.unit.agent_engine.test_service import service_with_outputs
from twm.services import AgentOutputError

from tests.unit.agent_engine.tolerance_support import (
    recorded,
    run,
    guide_plan,
)


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


def test_places_differing_only_by_whitespace_and_case_are_one_place(monkeypatch):
    output = guide_with_a_repeated_place(recorded("guide", "rishikesh-start"))
    output["state_delta"]["planner_state"]["places"] = ["Ram Jhula ", "ram jhula", "Triveni Ghat"]

    response, attempts, _ = run(monkeypatch, "guide", output)

    assert attempts == 1
    assert response["state_delta"]["planner_state"]["places"] == ["Ram Jhula", "Triveni Ghat"]
