"""API tests for stateless Guide agent execution."""

import json
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from twm.prompt_registry import PromptRelease, load_prompt_release
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
        "explicit_changes": [],
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
    engine.atlas = AsyncMock()
    return engine


def atlas_output() -> dict:
    general_reference = {
        "status": "GENERAL_GUIDANCE",
        "source_title": None,
        "source_url": None,
    }
    return {
        "final_itinerary": {
            "trip_summary": {
                "title": "Three friends in Rishikesh",
                "destinations": ["Rishikesh"],
                "duration_days": 1,
                "travelers": 3,
                "date_range": None,
                "overview": "A calm day around the approved riverside places.",
                "route_rationale": "The day keeps nearby places together.",
            },
            "travel_options": [],
            "stay_options": [],
            "days": [
                {
                    "day_number": 1,
                    "date": None,
                    "title": "Ram Jhula and Triveni Ghat",
                    "primary_location": "Rishikesh",
                    "summary": "An unhurried riverside day.",
                    "timeline": [
                        {
                            "start_time": "Morning",
                            "end_time": None,
                            "kind": "ACTIVITY",
                            "title": "Ram Jhula",
                            "location": "Rishikesh",
                            "detail": "Visit at a comfortable pace.",
                            "movement_guidance": None,
                            "estimated_cost_low": 0,
                            "estimated_cost_high": 0,
                            "reference": general_reference,
                        }
                    ],
                    "seasonal_guidance": "Carry weather-appropriate layers.",
                    "permit_or_ticket_guidance": "Check current local guidance.",
                    "backup_plan": None,
                }
            ],
            "budget_summary": {
                "currency": "INR",
                "lines": [
                    {
                        "category": "Local movement",
                        "amount_low": 500,
                        "amount_high": 800,
                        "note": "General range for three travelers.",
                    },
                    {
                        "category": "Meals",
                        "amount_low": 1200,
                        "amount_high": 1800,
                        "note": "General range for three travelers.",
                    },
                ],
                "total_low": 999,
                "total_high": 999,
                "budget_fit": "Within the stated ceiling.",
            },
            "practical_notes": [],
            "sources": [],
        },
        "unresolved": [],
    }


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
        prompt_release=PromptRelease("guide", "1.1.0", "prompt"),
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
        "agent_meta": {"agent": "guide", "prompt_version": "1.1.0"},
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


def test_atlas_api_forwards_finalized_context_and_plan(
    api_client: TestClient,
) -> None:
    engine = async_engine()
    engine.atlas.return_value = AgentExecution(
        response=atlas_output(),
        prompt_release=PromptRelease("atlas", "1.0.0", "prompt"),
    )
    set_engine(api_client, engine)
    payload = {
        "trip_context": {
            "origin": "Delhi",
            "travelers": 3,
            "budget": "INR 50000",
            "exclusions": ["river rafting"],
        },
        "working_plan": {
            "destinations": ["Rishikesh"],
            "duration_days": 1,
            "approved_places": ["Ram Jhula", "Triveni Ghat"],
            "days": [
                {
                    "day_number": 1,
                    "places": ["Ram Jhula", "Triveni Ghat"],
                }
            ],
        },
    }

    response = api_client.post("/atlas", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["agent_meta"] == {
        "agent": "atlas",
        "prompt_version": "1.0.0",
    }
    assert body["final_itinerary"]["budget_summary"]["total_low"] == 1700
    assert body["final_itinerary"]["budget_summary"]["total_high"] == 2600
    engine.atlas.assert_awaited_once_with(payload, None)


def test_atlas_api_uses_prompt_schema_and_common_validation(
    api_client: TestClient,
) -> None:
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(atlas_output()))
    )
    engine = AgentExecutionService(adapter, logger_for_test(), "test-engine")
    set_engine(api_client, engine)

    response = api_client.post(
        "/atlas",
        json={
            "trip_context": {"origin": "Delhi", "travelers": 3},
            "working_plan": {
                "destinations": ["Rishikesh"],
                "duration_days": 1,
                "approved_places": ["Ram Jhula"],
                "days": [{"day_number": 1, "places": ["Ram Jhula"]}],
            },
        },
    )

    assert response.status_code == 200
    release = load_prompt_release("atlas")
    agent, invocation = adapter.invoke.await_args.args
    assert agent == "atlas"
    assert invocation.system_prompt.startswith(
        f"{release.content}\n\nOUTPUT CONTRACT:\n"
    )
    assert '"final_itinerary"' in invocation.system_prompt
    framed_input = json.loads(invocation.user_prompt.split("\n", 1)[1])
    assert framed_input["trip_state"]["working_plan"]["approved_places"] == [
        "Ram Jhula"
    ]


def test_atlas_rejects_plan_that_does_not_allocate_approved_places(
    api_client: TestClient,
) -> None:
    engine = async_engine()
    set_engine(api_client, engine)

    response = api_client.post(
        "/atlas",
        json={
            "working_plan": {
                "destinations": ["Rishikesh"],
                "duration_days": 1,
                "approved_places": ["Ram Jhula", "Triveni Ghat"],
                "days": [{"day_number": 1, "places": ["Ram Jhula"]}],
            }
        },
    )

    assert response.status_code == 422
    engine.atlas.assert_not_awaited()
