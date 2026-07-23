"""API contract regressions derived from the five Testcases_v1 classes."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from twm.routers import trip_matcher
from twm.services import (
    AgentExecutionService,
    AgentInvocationResult,
)
from twm.telemetry import (
    InMemorySink,
    PayloadMode,
    TelemetryLogger,
    TelemetrySettings,
)


SCOUT_CASES = [
    pytest.param(
        {
            "trip_purpose": "honeymoon",
            "budget": "4 lakh + tickets",
            "travel_month": "September",
            "duration_nights": "4-5 nights",
            "stay_type": "all inclusive",
            "destination_scope": "India or visa free/on arrival countries",
            "india_preference": "high mountains",
            "international_preference": "beach front",
            "traveler_nationality": "Indian",
        },
        "matcher",
        {"origin", "origin_city"},
        id="tc1-honeymoon-qualifiers",
    ),
    pytest.param(
        {
            "travel_dates": "20-25 August",
            "origin_city": "Delhi",
            "travel_mode": "bus",
            "duration_days": "2-3",
            "group_description": "friend and I",
            "destination_type": "hill station",
            "safety_concern": "relatively safe from landslides",
            "weather_preference": "pleasant weather",
            "destinations_considered": ["Landour-Mussoorie"],
        },
        "matcher",
        set(),
        id="tc2-relative-monsoon-safety",
    ),
    pytest.param(
        {
            "destinations_compared": ["Scotland", "Ireland"],
            "travel_month": "late September",
            "duration_days": 10,
            "travel_companion": "partner",
            "preferences": [
                "dramatic coastal scenery",
                "historic ruins",
                "evening pub culture",
                "photography",
                "traditional music",
                "relaxed village atmosphere",
            ],
            "concerns": ["driving and navigation stress"],
        },
        "advise",
        set(),
        id="tc3-conditional-comparison-advice",
    ),
    pytest.param(
        {
            "destination": "Spiti Valley",
            "travel_windows": ["July/August", "September"],
            "comparison_requested": True,
            "destination_rainfall_qualifier": "Spiti itself receives limited rainfall",
            "route_concerns": [
                "monsoon effects on approach roads",
                "landslides",
                "water crossings",
            ],
        },
        "advise",
        set(),
        id="tc4-route-specific-season-advice",
    ),
    pytest.param(
        {
            "travel_month": "July",
            "seasonal_relevance": "right now or this time of year",
            "destination_scope": ["Kerala", "Karnataka"],
            "duration_days": "4-5",
            "travel_mode": "car",
            "trip_shape": "multiple-place road trip",
        },
        "matcher",
        {"origin", "origin_city", "starting_city"},
        id="tc5-circuit-readiness",
    ),
]


def _engine(*raw_outputs: dict) -> tuple[AgentExecutionService, AsyncMock]:
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=[
            AgentInvocationResult(raw_output=json.dumps(output))
            for output in raw_outputs
        ]
    )
    logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 16_384),
        InMemorySink(),
    )
    return AgentExecutionService(adapter, logger, "test-engine"), adapter


def _set_engine(api_client: TestClient, engine: AgentExecutionService) -> None:
    api_client.app.dependency_overrides[trip_matcher.get_engine] = lambda: engine


def _criterion(
    criterion_id: str,
    label: str,
    path: str,
    requirement_type: str = "PREFERENCE",
) -> dict:
    return {
        "id": criterion_id,
        "label": label,
        "requirement_type": requirement_type,
        "source_context_paths": [path],
    }


def _evaluation(criterion_id: str, outcome: str = "MATCH") -> dict:
    evaluation = {
        "criterion_id": criterion_id,
        "outcome": outcome,
        "conclusion": f"Conclusion for {criterion_id}.",
        "details": [{"type": "bullets", "items": [f"Evidence for {criterion_id}."]}],
    }
    if outcome in {"TRADEOFF", "MISMATCH"}:
        evaluation["tradeoffs"] = [f"Disclosed limitation for {criterion_id}."]
    return evaluation


def _success_output(criteria: list[dict], evaluations: list[dict]) -> dict:
    return {
        "status": "SUCCESS",
        "message": "Recommendation summary.",
        "state_delta": {
            "matcher_state": {"conversation_context": {"awaiting": None}}
        },
        "trip_type": "single",
        "traveler_criteria": criteria,
        "options": [
            {
                "rank": 1,
                "type": "single",
                "name": "Representative option",
                "destination_id": "representative-option",
                "summary": "Overall fit.",
                "evaluations": evaluations,
            }
        ],
    }


def _post_meridian(api_client: TestClient, output: dict):
    engine, _ = _engine(output)
    _set_engine(api_client, engine)
    return api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            },
            "message": "Find suitable options.",
        },
    )


@pytest.mark.parametrize(("trip_context", "intent", "forbidden_keys"), SCOUT_CASES)
def test_testcases_v1_scout_api_preserves_supplied_context_and_routing(
    api_client: TestClient,
    trip_context: dict,
    intent: str,
    forbidden_keys: set[str],
) -> None:
    output = {
        "message": "Traveler-specific response." if intent == "advise" else "",
        "state_delta": {"trip_context": trip_context},
        "intent": intent,
    }
    engine, _ = _engine(output)
    _set_engine(api_client, engine)

    response = api_client.post(
        "/scout",
        json={
            "trip_state": {
                "stage": "new",
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
            },
            "message": "Representative Testcases_v1 traveler request.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state_delta"]["trip_context"] == trip_context
    assert forbidden_keys.isdisjoint(body["state_delta"]["trip_context"])
    assert body["intent"] == intent


def test_tc1_api_preserves_budget_qualifier_and_exact_ask_coverage(
    api_client: TestClient,
) -> None:
    criteria = [
        _criterion(
            "honeymoon",
            "Suitable for a honeymoon",
            "trip_context.trip_purpose",
            "HARD",
        ),
        _criterion(
            "budget_excluding_tickets",
            "4 lakh excluding tickets",
            "trip_context.budget",
            "HARD",
        ),
        _criterion(
            "entry",
            "Visa free or visa on arrival when international",
            "trip_context.destination_scope",
            "HARD",
        ),
        _criterion("september", "September travel", "trip_context.travel_month"),
        _criterion("stay", "All-inclusive stay", "trip_context.stay_type"),
        _criterion(
            "setting",
            "High mountains in India or beachfront internationally",
            "trip_context.setting_preference",
        ),
    ]
    response = _post_meridian(
        api_client,
        _success_output(
            criteria,
            [
                _evaluation("honeymoon"),
                _evaluation("budget_excluding_tickets"),
                _evaluation("entry"),
                _evaluation("september"),
                _evaluation("stay", "TRADEOFF"),
                _evaluation("setting", "MISMATCH"),
            ],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    expected = {criterion["id"] for criterion in criteria}
    assert {criterion["id"] for criterion in body["traveler_criteria"]} == expected
    assert {
        evaluation["criterion_id"]
        for evaluation in body["options"][0]["evaluations"]
    } == expected
    assert body["options"][0]["evaluations"][-1]["tradeoffs"]


def test_tc2_api_keeps_relative_safety_and_discloses_mismatch(
    api_client: TestClient,
) -> None:
    criteria = [
        _criterion(
            "relative_safety",
            "Relatively safe from monsoon landslides",
            "trip_context.safety_concern",
        ),
        _criterion(
            "hill_station",
            "Hill-station destination",
            "trip_context.destination_type",
        ),
        _criterion(
            "bus_access",
            "Practical bus access from Delhi",
            "trip_context.travel_mode",
        ),
    ]
    response = _post_meridian(
        api_client,
        _success_output(
            criteria,
            [
                _evaluation("relative_safety", "TRADEOFF"),
                _evaluation("hill_station", "MISMATCH"),
                _evaluation("bus_access"),
            ],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert "Relatively" in body["traveler_criteria"][0]["label"]
    mismatch = body["options"][0]["evaluations"][1]
    assert mismatch["outcome"] == "MISMATCH"
    assert mismatch["tradeoffs"]


def test_tc5_api_requires_one_material_clarification_without_options(
    api_client: TestClient,
) -> None:
    question = "Which city will the road trip start from?"
    response = _post_meridian(
        api_client,
        {
            "status": "NEEDS_CLARIFICATION",
            "message": question,
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {
                        "awaiting": "starting_city",
                        "last_meridian_message": question,
                    }
                }
            },
            "options": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["options"] == []
    assert "traveler_criteria" not in body
    assert body["state_delta"]["matcher_state"]["conversation_context"] == {
        "awaiting": "starting_city",
        "last_meridian_message": question,
    }


def test_tc5_circuit_api_covers_route_feasibility_for_every_option(
    api_client: TestClient,
) -> None:
    criteria = [
        _criterion(
            "starting_point",
            "Disclosed starting-point assumption",
            "trip_context.starting_city",
        ),
        _criterion(
            "route_feasibility",
            "Route, daily driving load, and total duration are consistent",
            "trip_context.route_requirements",
            "HARD",
        ),
        _criterion(
            "monsoon",
            "July monsoon implications",
            "trip_context.seasonal_relevance",
        ),
    ]
    output = _success_output(
        criteria,
        [
            _evaluation("starting_point"),
            _evaluation("route_feasibility"),
            _evaluation("monsoon", "TRADEOFF"),
        ],
    )
    output["trip_type"] = "circuit"
    output["options"][0].update(
        {
            "type": "circuit",
            "destination_id": None,
            "circuit_id": "representative-circuit",
        }
    )
    response = _post_meridian(api_client, output)

    assert response.status_code == 200
    body = response.json()
    assert body["trip_type"] == "circuit"
    assert {
        evaluation["criterion_id"]
        for evaluation in body["options"][0]["evaluations"]
    } == {criterion["id"] for criterion in criteria}


def test_representative_api_rejects_one_unevaluated_traveler_ask(
    api_client: TestClient,
) -> None:
    criteria = [
        _criterion("duration", "Trip duration", "trip_context.duration_days"),
        _criterion("budget", "Total group budget", "trip_context.budget_total"),
    ]
    valid = _success_output(
        criteria, [_evaluation("duration"), _evaluation("budget")]
    )
    invalid = deepcopy(valid)
    invalid["options"][0]["evaluations"].pop()
    engine, _ = _engine(invalid, invalid)
    _set_engine(api_client, engine)

    response = api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            },
            "message": "Find suitable options.",
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The travel assistant returned an invalid response."
    }
