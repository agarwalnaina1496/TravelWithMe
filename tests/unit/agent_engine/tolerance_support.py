"""Shared helpers for the output-tolerance tests (TWM-234)."""

import asyncio
import glob
import json


from tests.factories import recommendation_option, traveler_criteria
from tests.unit.agent_engine.test_service import service_with_outputs
from twm.services.agent_engine import AgentExecutionService
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


def atlas() -> dict:
    return recorded("atlas", sorted({p.split("__")[1] for p in glob.glob(FIXTURES.format(agent="atlas", case="*"))})[0])


def first_item(output: dict) -> dict:
    return output["final_itinerary"]["days"][0]["timeline"][0]


def success_with(mutate):
    output = meridian_success()
    mutate(output)
    return output


def cost_detail(**fields) -> dict:
    return {"type": "cost_breakdown", "currency": "INR", **fields}


def guide_plan() -> dict:
    return recorded("guide", "anything-else-answered-generates-plan")


def engine_with(monkeypatch, outputs, retry=None):
    engine, adapter = service_with_outputs(monkeypatch, *outputs)
    engine = AgentExecutionService(adapter, engine._logger, "test-engine", retry=retry)
    return engine, adapter


SCOUT_REPLY = '{"message": "Hello there", "intent": "advise"}'


def scrambled_ranks(output: dict) -> dict:
    output["options"][0]["rank"] = 2
    output["options"][1]["rank"] = 2
    return output
