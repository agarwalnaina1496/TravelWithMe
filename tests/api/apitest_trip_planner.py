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
        "state_delta": {
            "trip_context": {
                "destinations": ["Rishikesh"],
                "trip_duration": 3,
                "preferences": ["relaxed"],
                "exclusions": ["river rafting"],
            },
            "planner_state": {
                "conversation_context": {"awaiting": None},
                "places": [
                    "Ram Jhula",
                    "Triveni Ghat",
                    "Neer Garh Waterfall",
                ],
                "day_plan": None,
            },
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
                "trip_duration": 1,
                "num_travelers": 3,
                "date_range": None,
                "overview": "A calm day around the approved riverside places.",
                "route_rationale": "The day keeps nearby places together.",
            },
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
                            "requires_advance_booking": False,
                            "booking_readiness": None,
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
            "assumptions": [
                {
                    "category": "dates",
                    "detail": "Assumed a start date since none was confirmed.",
                }
            ],
        },
        "unresolved": [],
    }


def set_engine(api_client: TestClient, engine: object) -> None:
    api_client.app.dependency_overrides[trip_planner.get_engine] = lambda: engine


def logger_for_test(sink: InMemorySink | None = None) -> TelemetryLogger:
    return TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 16_384),
        sink if sink is not None else InMemorySink(),
    )


def test_guide_request_logs_share_one_correlated_request_id(
    api_client: TestClient,
) -> None:
    """Regression for TWM-124: before the correlation middleware covered
    /guide, every log line in one request got its own random request_id and
    could not be correlated as a single turn in Axiom."""
    engine = async_engine()
    engine.guide.return_value = AgentExecution(
        response=guide_places_output(),
        prompt_release=PromptRelease("guide", "1.1.0", "prompt"),
    )
    set_engine(api_client, engine)
    sink = InMemorySink()
    api_client.app.dependency_overrides[trip_planner.get_logger] = lambda: logger_for_test(sink)

    response = api_client.post(
        "/guide",
        json={"event": "START", "trip_state": {"trip_context": {}}},
    )

    assert response.status_code == 200
    assert len(sink.events) >= 2
    request_ids = {event["request_id"] for event in sink.events}
    assert len(request_ids) == 1


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
                "origin_city": "Delhi",
                "destinations": ["Rishikesh"],
                "trip_duration": 3,
                "num_travelers": 3,
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
        "outcome": "continue",
        "agent_meta": {"agent": "guide", "prompt_version": "1.1.0"},
    }
    engine.guide.assert_awaited_once_with(
        {
            "trip_context": payload["trip_state"]["trip_context"],
            "planner_state": {
                "conversation_context": {"awaiting": None},
                "places": [],
                "day_plan": [],
            },
            "guide_event": "START",
        },
        "Plan a relaxed trip.",
    )


def test_guide_api_accepts_expanded_awaiting_values(api_client: TestClient) -> None:
    engine = async_engine()
    engine.guide.return_value = AgentExecution(
        response={
            "message": "Where are you starting your trip from?",
            "state_delta": {
                "trip_context": {"trip_duration": 5},
                "planner_state": {
                    "conversation_context": {"awaiting": "origin_city"},
                },
            },
        },
        prompt_release=PromptRelease("guide", "2.1.0", "prompt"),
    )
    set_engine(api_client, engine)
    payload = {
        "event": "START",
        "trip_state": {
            "trip_context": {"destinations": ["Ladakh"], "trip_duration": 5},
        },
        "message": "Plan a five day trip to Ladakh.",
    }

    response = api_client.post("/guide", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["state_delta"]["planner_state"]["conversation_context"][
        "awaiting"
    ] == "origin_city"


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
                    "trip_duration": 3,
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
    assert '"state_delta"' in invocation.system_prompt
    framed_input = json.loads(invocation.user_prompt.split("\n", 1)[1])
    assert framed_input["trip_state"]["guide_event"] == "START"
    assert framed_input["trip_state"]["trip_context"] == {
        "destinations": ["Rishikesh"],
        "trip_duration": 3,
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
            "origin_city": "Delhi",
            "num_travelers": 3,
            "budget": "INR 50000",
            "exclusions": ["river rafting"],
        },
        "working_plan": {
            "destinations": ["Rishikesh"],
            "trip_duration": 1,
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
    engine.atlas.assert_awaited_once_with({**payload, "confirmed_anchors": []}, None)


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
            "trip_context": {"origin_city": "Delhi", "num_travelers": 3},
            "working_plan": {
                "destinations": ["Rishikesh"],
                "trip_duration": 1,
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


def test_atlas_rejects_timeline_item_with_inconsistent_booking_readiness(
    api_client: TestClient,
) -> None:
    invalid_output = atlas_output()
    invalid_output["final_itinerary"]["days"][0]["timeline"][0][
        "requires_advance_booking"
    ] = True
    invalid_output["final_itinerary"]["days"][0]["timeline"][0][
        "booking_readiness"
    ] = None
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(invalid_output))
    )
    engine = AgentExecutionService(adapter, logger_for_test(), "test-engine")
    set_engine(api_client, engine)

    response = api_client.post(
        "/atlas",
        json={
            "trip_context": {"origin_city": "Delhi", "num_travelers": 3},
            "working_plan": {
                "destinations": ["Rishikesh"],
                "trip_duration": 1,
                "approved_places": ["Ram Jhula"],
                "days": [{"day_number": 1, "places": ["Ram Jhula"]}],
            },
        },
    )

    assert response.status_code == 502
    assert adapter.invoke.await_count == 1


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
                "trip_duration": 1,
                "approved_places": ["Ram Jhula", "Triveni Ghat"],
                "days": [{"day_number": 1, "places": ["Ram Jhula"]}],
            }
        },
    )

    assert response.status_code == 422
    engine.atlas.assert_not_awaited()
