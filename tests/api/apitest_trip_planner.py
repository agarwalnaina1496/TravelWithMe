"""API tests for stateless Guide agent execution."""

import json
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from twm.prompts import PromptRelease, load_prompt_release
from twm.routers import trip_planner
from twm.services import (
    AgentExecution,
    AgentExecutionService,
    AgentInvocationResult,
)
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


def guide_places_output() -> dict:
    return {
        "message": "Here are the places I suggest. Tell me what to change.",
        "guide_state": {
            "phase": "PLACES_DRAFT",
            "destinations": ["Rishikesh"],
            "duration_days": 3,
            "start_date": None,
            "places": [
                "Ram Jhula",
                "Triveni Ghat",
                "Neer Garh Waterfall",
            ],
            "day_plan": [],
            "preferences": ["relaxed"],
            "exclusions": ["river rafting"],
            "applied_changes": [],
            "pending_clarification": None,
        },
    }


def async_engine() -> Mock:
    engine = Mock(spec=[])
    engine.guide = AsyncMock()
    return engine


def set_engine(api_client: TestClient, engine: object) -> None:
    api_client.app.dependency_overrides[trip_planner.get_engine] = lambda: engine


def logger_for_test() -> TelemetryLogger:
    return TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 16_384),
        InMemorySink(),
    )


def test_guide_api_forwards_event_state_and_message(api_client: TestClient) -> None:
    engine = async_engine()
    engine.guide.return_value = AgentExecution(
        response=guide_places_output(),
        prompt_release=PromptRelease("guide", "1.0.0", "prompt"),
    )
    set_engine(api_client, engine)
    payload = {
        "event": "START",
        "trip_state": {
            "trip_context": {
                "origin": "Delhi",
                "destinations": ["Rishikesh"],
                "duration_days": 3,
                "travelers": 3,
                "budget": "INR 50000",
                "exclusions": ["river rafting"],
            }
        },
        "message": "Plan a relaxed trip.",
    }

    response = api_client.post("/guide", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        **guide_places_output(),
        "agent_meta": {"agent": "guide", "prompt_version": "1.0.0"},
    }
    engine.guide.assert_awaited_once_with(
        {
            "trip_context": payload["trip_state"]["trip_context"],
            "guide_state": {
                "phase": None,
                "destinations": [],
                "duration_days": None,
                "start_date": None,
                "places": [],
                "day_plan": [],
                "preferences": [],
                "exclusions": [],
                "applied_changes": [],
                "pending_clarification": None,
            },
            "guide_event": "START",
        },
        "Plan a relaxed trip.",
    )


def test_guide_api_uses_prompt_schema_and_common_validation(
    api_client: TestClient,
) -> None:
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(
            raw_output=json.dumps(guide_places_output())
        )
    )
    engine = AgentExecutionService(adapter, logger_for_test(), "test-engine")
    set_engine(api_client, engine)

    response = api_client.post(
        "/guide",
        json={
            "event": "START",
            "trip_state": {
                "trip_context": {
                    "destinations": ["Rishikesh"],
                    "duration_days": 3,
                }
            },
        },
    )

    assert response.status_code == 200
    release = load_prompt_release("guide")
    agent, invocation = adapter.invoke.await_args.args
    assert agent == "guide"
    assert invocation.system_prompt.startswith(
        f"{release.content}\n\nOUTPUT CONTRACT:\n"
    )
    assert '"guide_state"' in invocation.system_prompt
    framed_input = json.loads(invocation.user_prompt.split("\n", 1)[1])
    assert framed_input["trip_state"]["guide_event"] == "START"
    assert framed_input["trip_state"]["trip_context"] == {
        "destinations": ["Rishikesh"],
        "duration_days": 3,
    }


def test_guide_traveler_message_requires_message(
    api_client: TestClient,
) -> None:
    engine = async_engine()
    set_engine(api_client, engine)

    response = api_client.post(
        "/guide",
        json={"event": "TRAVELER_MESSAGE", "trip_state": {}},
    )

    assert response.status_code == 422
    engine.guide.assert_not_awaited()
