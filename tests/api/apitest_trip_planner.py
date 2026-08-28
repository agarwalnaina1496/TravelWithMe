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
                    "notes": [
                        {
                            "category": "Weather",
                            "title": "Carry layers",
                            "detail": "Carry weather-appropriate layers.",
                            "reference": general_reference,
                        },
                        {
                            "category": "Local guidance",
                            "title": "Check current guidance",
                            "detail": "Check current local guidance.",
                            "reference": general_reference,
                        },
                    ],
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
        json={"event": "MESSAGE", "trip_state": {"trip_context": {}}},
    )

    assert response.status_code == 200
    assert len(sink.events) >= 2
    request_ids = {event["request_id"] for event in sink.events}
    assert len(request_ids) == 1


def test_guide_api_forwards_state_and_message_with_no_guide_event(api_client: TestClient) -> None:
    engine = async_engine()
    engine.guide.return_value = AgentExecution(
        response=guide_places_output(),
        prompt_release=PromptRelease("guide", "1.1.0", "prompt"),
    )
    set_engine(api_client, engine)
    payload = {
        "event": "MESSAGE",
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
        "event": "MESSAGE",
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
            "event": "MESSAGE",
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
    # No guide_event field — every MESSAGE turn is handled identically
    # (guide.md), so there is nothing for Guide to branch on.
    assert "guide_event" not in framed_input["trip_state"]
    assert framed_input["trip_state"]["trip_context"] == {
        "destinations": ["Rishikesh"],
        "trip_duration": 3,
    }


def test_guide_api_accepts_a_message_less_turn(api_client: TestClient) -> None:
    """The entry-command collapse means message is genuinely optional on a
    MESSAGE turn — the cold Discover-path transition (a destination was
    just selected, nothing for the traveler to say yet) has none, and
    Guide simply checks the gates and asks the first missing one."""
    engine = async_engine()
    engine.guide.return_value = AgentExecution(
        response=guide_places_output(),
        prompt_release=PromptRelease("guide", "1.1.0", "prompt"),
    )
    set_engine(api_client, engine)

    response = api_client.post(
        "/guide",
        json={"event": "MESSAGE", "trip_state": {}},
    )

    assert response.status_code == 200
    engine.guide.assert_awaited_once()


def test_guide_api_rejects_a_blank_message_when_provided(
    api_client: TestClient,
) -> None:
    engine = async_engine()
    set_engine(api_client, engine)

    response = api_client.post(
        "/guide",
        json={"event": "MESSAGE", "trip_state": {}, "message": "   "},
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


def test_atlas_api_returns_odisha_route_with_canonical_movement_endpoints(
    api_client: TestClient,
) -> None:
    """Regression for TWM-200: Atlas must be able to represent atomic
    movements with canonical city endpoints separate from scenic-route
    display copy, even across a multi-day, multi-leg route."""
    general_reference = {
        "status": "GENERAL_GUIDANCE",
        "source_title": None,
        "source_url": None,
    }
    legs = [
        ("Bangalore", "Bhubaneswar", None),
        ("Bhubaneswar", "Puri", "Bhubaneswar to Puri Highway"),
        ("Puri", "Konark", "Travel along Marine Drive from Puri to Konark"),
        ("Konark", "Bhubaneswar", "Konark to Bhubaneswar via Pipili"),
        ("Bhubaneswar", "Bangalore", None),
    ]
    output = atlas_output()
    output["final_itinerary"]["days"] = [
        {
            "day_number": index + 1,
            "title": f"{from_city} to {to_city}",
            "primary_location": to_city,
            "summary": "Travel day.",
            "timeline": [
                {
                    "start_time": "Morning",
                    "end_time": None,
                    "kind": "TRAVEL",
                    "title": f"{from_city} to {to_city}",
                    "location": narrative_location or f"{from_city} to {to_city}",
                    "detail": "Travel between cities.",
                    "movement_guidance": None,
                    "from_city": from_city,
                    "to_city": to_city,
                    "estimated_cost_low": 0,
                    "estimated_cost_high": 0,
                    "reference": general_reference,
                    "requires_advance_booking": False,
                    "booking_readiness": None,
                }
            ],
            "notes": [
                {
                    "category": "Weather",
                    "title": "Carry layers",
                    "detail": "Carry weather-appropriate layers.",
                    "reference": general_reference,
                },
                {
                    "category": "Local guidance",
                    "title": "Check current guidance",
                    "detail": "Check current local guidance.",
                    "reference": general_reference,
                },
            ],
            "backup_plan": None,
        }
        for index, (from_city, to_city, narrative_location) in enumerate(legs)
    ]
    output["final_itinerary"]["trip_summary"]["destinations"] = [
        "Bangalore",
        "Bhubaneswar",
        "Puri",
        "Konark",
    ]
    output["final_itinerary"]["trip_summary"]["trip_duration"] = len(legs)
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(output))
    )
    engine = AgentExecutionService(adapter, logger_for_test(), "test-engine")
    set_engine(api_client, engine)

    response = api_client.post(
        "/atlas",
        json={
            "trip_context": {"origin_city": "Bangalore", "num_travelers": 2},
            "working_plan": {
                "destinations": ["Bhubaneswar", "Puri", "Konark"],
                "trip_duration": len(legs),
                "approved_places": [],
                "days": [
                    {"day_number": index + 1, "places": []}
                    for index in range(len(legs))
                ],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    returned_legs = [
        (day["timeline"][0]["from_city"], day["timeline"][0]["to_city"])
        for day in body["final_itinerary"]["days"]
    ]
    assert returned_legs == [
        ("Bangalore", "Bhubaneswar"),
        ("Bhubaneswar", "Puri"),
        ("Puri", "Konark"),
        ("Konark", "Bhubaneswar"),
        ("Bhubaneswar", "Bangalore"),
    ]
    # location carries the scenic/via narration; it must never leak
    # into the canonical from_city/to_city endpoints UI sends to feasibility.
    assert body["final_itinerary"]["days"][2]["timeline"][0]["location"] == (
        "Travel along Marine Drive from Puri to Konark"
    )
    assert body["final_itinerary"]["days"][2]["timeline"][0]["from_city"] == "Puri"
    assert body["final_itinerary"]["days"][2]["timeline"][0]["to_city"] == "Konark"


def test_atlas_rejects_travel_item_with_only_one_movement_endpoint(
    api_client: TestClient,
) -> None:
    """Fail-closed regression for TWM-200: a TRAVEL item must never emit a
    fabricated or partial endpoint pair."""
    invalid_output = atlas_output()
    invalid_output["final_itinerary"]["days"][0]["timeline"][0]["kind"] = "TRAVEL"
    invalid_output["final_itinerary"]["days"][0]["timeline"][0]["from_city"] = "Delhi"
    invalid_output["final_itinerary"]["days"][0]["timeline"][0]["to_city"] = None
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


def test_atlas_rejects_movement_endpoints_on_a_non_travel_timeline_item(
    api_client: TestClient,
) -> None:
    """Fail-closed regression for TWM-200: only TRAVEL items may carry
    canonical movement endpoints — an ACTIVITY item must not smuggle them in."""
    invalid_output = atlas_output()
    invalid_output["final_itinerary"]["days"][0]["timeline"][0]["from_city"] = "Delhi"
    invalid_output["final_itinerary"]["days"][0]["timeline"][0]["to_city"] = "Agra"
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


def test_atlas_api_returns_structured_exact_departure_date(
    api_client: TestClient,
) -> None:
    """Regression for TWM-200: a TRAVEL item with a confirmed exact date
    must pass its structured departure_date through unfabricated."""
    output = atlas_output()
    timeline_item = output["final_itinerary"]["days"][0]["timeline"][0]
    timeline_item["kind"] = "TRAVEL"
    timeline_item["from_city"] = "Delhi"
    timeline_item["to_city"] = "Rishikesh"
    timeline_item["departure_date"] = "2026-10-05"
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(output))
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
    body = response.json()
    returned_item = body["final_itinerary"]["days"][0]["timeline"][0]
    assert returned_item["departure_date"] == "2026-10-05"
    assert returned_item["departure_month"] is None


def test_atlas_api_returns_structured_departure_month(
    api_client: TestClient,
) -> None:
    """Regression for TWM-200: a TRAVEL item with only a confirmed
    year+month must pass its structured departure_month through, never a
    guessed exact date."""
    output = atlas_output()
    timeline_item = output["final_itinerary"]["days"][0]["timeline"][0]
    timeline_item["kind"] = "TRAVEL"
    timeline_item["from_city"] = "Delhi"
    timeline_item["to_city"] = "Rishikesh"
    timeline_item["departure_month"] = "2026-10"
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(output))
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
    body = response.json()
    returned_item = body["final_itinerary"]["days"][0]["timeline"][0]
    assert returned_item["departure_month"] == "2026-10"
    assert returned_item["departure_date"] is None


def test_atlas_rejects_travel_item_with_both_departure_date_and_month(
    api_client: TestClient,
) -> None:
    """Fail-closed regression for TWM-200: departure_date and
    departure_month are mutually exclusive precision levels."""
    output = atlas_output()
    timeline_item = output["final_itinerary"]["days"][0]["timeline"][0]
    timeline_item["kind"] = "TRAVEL"
    timeline_item["from_city"] = "Delhi"
    timeline_item["to_city"] = "Rishikesh"
    timeline_item["departure_date"] = "2026-10-05"
    timeline_item["departure_month"] = "2026-10"
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(output))
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


def test_atlas_rejects_unvalidated_free_text_departure_month(
    api_client: TestClient,
) -> None:
    """Fail-closed regression for TWM-200: a bare month label such as
    "October" must never pass as a structured departure_month — Atlas
    must never guess a year to satisfy the YYYY-MM shape."""
    output = atlas_output()
    timeline_item = output["final_itinerary"]["days"][0]["timeline"][0]
    timeline_item["kind"] = "TRAVEL"
    timeline_item["from_city"] = "Delhi"
    timeline_item["to_city"] = "Rishikesh"
    timeline_item["departure_month"] = "October"
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(output))
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


def test_atlas_rejects_departure_date_on_a_non_travel_timeline_item(
    api_client: TestClient,
) -> None:
    """Fail-closed regression for TWM-200: only TRAVEL items may carry
    structured departure-date precision — an ACTIVITY item must not
    smuggle a fabricated exact date in."""
    invalid_output = atlas_output()
    invalid_output["final_itinerary"]["days"][0]["timeline"][0]["departure_date"] = (
        "2026-10-05"
    )
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


def _stay_tier(tier: str, low: int, high: int) -> dict:
    return {"tier": tier, "estimated_cost_low": low, "estimated_cost_high": high}


def _post_atlas_with_day_field(day_overrides: dict) -> tuple:
    output = atlas_output()
    output["final_itinerary"]["days"][0].update(day_overrides)
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        return_value=AgentInvocationResult(raw_output=json.dumps(output))
    )
    engine = AgentExecutionService(adapter, logger_for_test(), "test-engine")
    return adapter, engine


def test_atlas_api_returns_a_valid_stay_price_estimate(api_client: TestClient) -> None:
    """Regression for TWM-204: a well-formed three-tier estimate for a day
    with an overnight stay passes through unfabricated."""
    adapter, engine = _post_atlas_with_day_field(
        {
            "stay_price_estimate": [
                _stay_tier("budget", 800, 1500),
                _stay_tier("mid_range", 1500, 3000),
                _stay_tier("premium", 3000, 6000),
            ]
        }
    )
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
    body = response.json()
    returned = body["final_itinerary"]["days"][0]["stay_price_estimate"]
    assert [tier["tier"] for tier in returned] == ["budget", "mid_range", "premium"]


def test_atlas_omits_stay_price_estimate_for_a_day_with_no_overnight_stay(
    api_client: TestClient,
) -> None:
    """Regression for TWM-204: absent stay_price_estimate is valid -- a
    day-trip/transit-only day must not be forced to fabricate a range."""
    adapter, engine = _post_atlas_with_day_field({})
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
    assert response.json()["final_itinerary"]["days"][0]["stay_price_estimate"] is None


def test_atlas_rejects_stay_price_estimate_with_wrong_tier_order(
    api_client: TestClient,
) -> None:
    adapter, engine = _post_atlas_with_day_field(
        {
            "stay_price_estimate": [
                _stay_tier("mid_range", 1500, 3000),
                _stay_tier("budget", 800, 1500),
                _stay_tier("premium", 3000, 6000),
            ]
        }
    )
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


def test_atlas_rejects_stay_price_estimate_missing_a_tier(
    api_client: TestClient,
) -> None:
    adapter, engine = _post_atlas_with_day_field(
        {
            "stay_price_estimate": [
                _stay_tier("budget", 800, 1500),
                _stay_tier("premium", 3000, 6000),
            ]
        }
    )
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


def test_atlas_rejects_stay_price_estimate_with_decreasing_tier_low(
    api_client: TestClient,
) -> None:
    """Regression for TWM-204: a lower tier's estimated_cost_low may never
    exceed a higher tier's -- e.g. premium priced below budget."""
    adapter, engine = _post_atlas_with_day_field(
        {
            "stay_price_estimate": [
                _stay_tier("budget", 800, 1500),
                _stay_tier("mid_range", 1500, 3000),
                _stay_tier("premium", 1000, 6000),
            ]
        }
    )
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
