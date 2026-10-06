"""Meridian slips heal on the first attempt; what stays rejected, and why (TWM-234)."""

import asyncio
import json
from copy import deepcopy

import pytest

from tests.factories import recommendation_option
from tests.unit.agent_engine.test_service import service_with_outputs
from twm.services import AgentOutputError

from tests.unit.agent_engine.tolerance_support import (
    scrambled_ranks,
    meridian_success,
    meridian_clarification,
    run,
    success_with,
    cost_detail,
)


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


@pytest.mark.parametrize(
    ("written", "amount"),
    [
        ("1,500", 1500.0),
        ("1,50,000", 150000.0),
        ("10,00,000", 1000000.0),
        ("1 500", 1500.0),
        ("₹ 12,500/-", 12500.0),
        ("Rs. 800", 800.0),
        ("INR 2,000.75", 2000.75),
    ],
)
def test_a_formatted_amount_is_read(monkeypatch, written, amount):
    output = success_with(
        lambda o: o["options"][0]["evaluations"][0].update(
            details=[cost_detail(per_person_total={"minimum": written, "maximum": written})]
        )
    )

    response, attempts, _ = run(monkeypatch, "meridian", output)

    assert attempts == 1
    cost = response["options"][0]["evaluations"][0]["details"][0]["per_person_total"]
    assert cost == {"minimum": amount, "maximum": amount}


@pytest.mark.parametrize("written", ["2 3", "12,34", "1,5000", "1-2 lakh", "1.5k"])
def test_a_number_that_could_mean_something_else_is_not_guessed(monkeypatch, written):
    output = json.dumps(
        success_with(
            lambda o: o["options"][0]["evaluations"][0].update(
                details=[cost_detail(per_person_total={"minimum": written, "maximum": written})]
            )
        )
    )
    engine, adapter = service_with_outputs(monkeypatch, output, output)

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.meridian({}, "message"))

    assert adapter.invoke.await_count == 2
