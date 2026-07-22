"""API tests for the Scout and Meridian router."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from twm.prompts import (
    PromptRelease,
    load_prompt_release,
    load_prompt_versions,
    validate_prompt_release_files,
)
from twm.routers import trip_matcher
from twm.services import (
    AgentAdapterTimeoutError,
    AgentExecution,
    AgentExecutionService,
    AgentInvocationResult,
    LangGraphAgentAdapter,
    LangGraphRuntime,
)
from twm.services.response_normalization import _normalize_meridian_response
from twm.schemas.scout import ScoutResponse
from tests.factories import recommendation_option, traveler_criteria
from twm.security import (
    MAX_CONTAINER_ITEMS,
    MAX_DATA_DEPTH,
    MAX_MESSAGE_CHARACTERS,
    MAX_PHASE_STATE_BYTES,
)
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings
from tests.unit.langgraph.fakes import FakeChatModel


def async_engine() -> Mock:
    engine = Mock(spec=[])
    engine.scout = AsyncMock()
    engine.meridian = AsyncMock()
    return engine


def set_engine(api_client: TestClient, engine: object) -> None:
    api_client.app.dependency_overrides[trip_matcher.get_engine] = lambda: engine


def set_logger(api_client: TestClient, logger: TelemetryLogger) -> None:
    api_client.app.dependency_overrides[trip_matcher.get_logger] = (
        lambda: logger
    )


def logger_for_test() -> TelemetryLogger:
    return TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 16_384),
        InMemorySink(),
    )


def common_engine(*outputs: dict) -> tuple[AgentExecutionService, AsyncMock]:
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=[
            AgentInvocationResult(raw_output=json.dumps(output))
            for output in outputs
        ]
    )
    return AgentExecutionService(adapter, logger_for_test(), "test-engine"), adapter


def meridian_success_output() -> dict:
    return {
        "status": "SUCCESS",
        "message": "The first option is the strongest overall fit.",
        "state_delta": {
            "matcher_state": {
                "conversation_context": {"awaiting": None}
            }
        },
        "trip_type": "single",
        "traveler_criteria": traveler_criteria(),
        "options": [recommendation_option()],
    }


def assert_meridian_api_rejects(
    api_client: TestClient, monkeypatch, output: dict
) -> None:
    engine = async_engine()
    engine.meridian.return_value = AgentExecution(
        response=output,
        prompt_release=PromptRelease("meridian", "2.0.0", "prompt"),
    )
    set_engine(api_client, engine)

    response = api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            }
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The travel assistant returned an invalid response."
    }


def test_active_phase_prompt_releases_are_complete() -> None:
    validate_prompt_release_files()

    assert load_prompt_versions() == {
        "scout": "1.7.0",
        "meridian": "1.6.0",
    }
    meridian_prompt = load_prompt_release("meridian").content
    assert "conversation_context.awaiting" in meridian_prompt
    assert "constraint_adjustment_suggestions" in meridian_prompt
    assert "traveler_criteria" in meridian_prompt
    assert "why_ranked_here" not in meridian_prompt
    assert "decision_summary" not in meridian_prompt
    assert '"version": "matcher_v2"' not in meridian_prompt
    assert '"relaxation_suggestions"' not in meridian_prompt


def test_scout_api_preserves_entry_contract(
    api_client: TestClient, monkeypatch
) -> None:
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 16_384), sink
    )
    set_logger(api_client, logger)
    engine = async_engine()
    engine.scout.return_value = AgentExecution(
        response={
            "message": "Here is a broad answer.",
            "state_delta": {"trip_context": {"region": "Uttarakhand"}},
            "intent": "advise",
        },
        prompt_release=PromptRelease("scout", "1.1.0", "prompt"),
    )
    set_engine(api_client, engine)
    payload = {
        "trip_state": {
            "stage": "new",
            "trip_context": {},
            "advisor_state": {},
        },
        "message": "Tell me about Uttarakhand.",
    }

    response = api_client.post(
        "/scout",
        json=payload,
        headers={
            "X-TWM-Request-ID": "request-1",
            "X-TWM-Trip-ID": "trip-1",
            "X-TWM-Turn-ID": "turn-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Here is a broad answer.",
        "state_delta": {"trip_context": {"region": "Uttarakhand"}},
        "intent": "advise",
        "agent_meta": {"agent": "scout", "prompt_version": "1.1.0"},
    }
    engine.scout.assert_called_once_with(
        {
            "stage": "new",
            "trip_context": {},
            "advisor_state": {
                "conversation_context": {"last_advisor_message": None}
            },
        },
        "Tell me about Uttarakhand.",
    )
    received, returning = sink.events
    assert received["message"] == "Received Scout request"
    assert received["payload"] == {
        "trip_state": {
            "stage": "new",
            "trip_context": {},
            "advisor_state": {"conversation_context": {}},
        },
        "message": "Tell me about Uttarakhand.",
    }
    assert returning["message"] == "Returning Scout response"
    assert returning["response"] == response.json()
    assert {
        (event["request_id"], event["trip_id"], event["turn_id"])
        for event in sink.events
    } == {("request-1", "trip-1", "turn-1")}


def test_scout_api_uses_common_prepared_invocation(
    api_client: TestClient, monkeypatch
) -> None:
    engine, adapter = common_engine(
        {
            "message": "A mountain trip can work well.",
            "state_delta": {"trip_context": {"region": "Uttarakhand"}},
            "intent": "advise",
        }
    )
    set_engine(api_client, engine)
    payload = {
        "trip_state": {"stage": "new", "trip_context": {}},
        "message": "Tell me about Uttarakhand.",
    }

    response = api_client.post("/scout", json=payload)

    assert response.status_code == 200
    release = load_prompt_release("scout")
    agent, invocation = adapter.invoke.await_args.args
    assert agent == "scout"
    assert invocation.system_prompt.startswith(release.content)
    assert '"intent"' in invocation.system_prompt
    assert "Tell me about Uttarakhand." in invocation.user_prompt


@pytest.mark.parametrize(
    "state_delta",
    [
        {"trip_context": {"selected_option": {"id": "ui-owned"}}},
        {"matcher_state": {"conversation_context": {"awaiting": "budget"}}},
        {"advisor_state": {"conversation_context": {}}},
    ],
)
def test_scout_response_rejects_non_owned_state_delta(
    state_delta: dict,
) -> None:
    with pytest.raises(ValidationError):
        ScoutResponse(
            message="Invalid state write.",
            state_delta=state_delta,
            intent="advise",
            agent_meta={"agent": "scout", "prompt_version": "1.2.0"},
        )


def test_meridian_api_forwards_phase_slice_and_message(
    api_client: TestClient, monkeypatch
) -> None:
    engine = async_engine()
    engine.meridian.return_value = AgentExecution(
        response={
            "status": "NEEDS_CLARIFICATION",
            "message": "What budget should I use?",
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {
                        "last_meridian_message": "What budget should I use?",
                        "awaiting": "budget",
                    }
                }
            },
            "options": [],
            "version": "matcher_v2",
        },
        prompt_release=PromptRelease("meridian", "1.0.0", "prompt"),
    )
    set_engine(api_client, engine)
    payload = {
        "trip_state": {
            "trip_context": {"destination_scope": "mountains"},
            "advisor_state": {
                "conversation_context": {
                    "last_advisor_message": "Here are the broad choices."
                }
            },
            "matcher_state": {},
        },
        "message": "Please narrow those down.",
    }

    response = api_client.post("/meridian", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "status": "NEEDS_CLARIFICATION",
        "state_delta": {
            "trip_context": {},
            "matcher_state": {
                "conversation_context": {
                    "last_meridian_message": "What budget should I use?",
                    "awaiting": "budget",
                }
            },
        },
        "message": "What budget should I use?",
        "options": [],
        "agent_meta": {"agent": "meridian", "prompt_version": "1.0.0"},
    }
    engine.meridian.assert_called_once_with(
        payload["trip_state"], "Please narrow those down."
    )


def test_meridian_api_uses_current_prompt_for_awaiting_continuation(
    api_client: TestClient, monkeypatch
) -> None:
    engine, adapter = common_engine(
        {
            "status": "SUCCESS",
            "message": "I used that clarification to update the matches.",
            "state_delta": {
                "trip_context": {"budget": "mid-range"},
                "matcher_state": {
                    "conversation_context": {
                        "last_meridian_message": "I used that clarification to update the matches.",
                        "awaiting": None,
                    }
                },
            },
            "trip_type": "single",
            "traveler_criteria": traveler_criteria(),
            "options": [recommendation_option()],
        }
    )
    set_engine(api_client, engine)
    payload = {
        "trip_state": {
            "trip_context": {"destination_scope": "mountains"},
            "advisor_state": {
                "conversation_context": {
                    "last_advisor_message": "A mountain trip can work well."
                }
            },
            "matcher_state": {
                "conversation_context": {
                    "last_meridian_message": "What budget range should I use?",
                    "awaiting": "budget",
                }
            },
        },
        "message": "Keep it mid-range.",
    }

    response = api_client.post("/meridian", json=payload)

    assert response.status_code == 200
    assert response.json()["agent_meta"] == {
        "agent": "meridian",
        "prompt_version": "1.6.0",
    }
    release = load_prompt_release("meridian")
    agent, invocation = adapter.invoke.await_args.args
    assert agent == "meridian"
    assert invocation.system_prompt.startswith(release.content)
    assert '"traveler_criteria"' in invocation.system_prompt
    assert "Keep it mid-range." in invocation.user_prompt


def test_meridian_api_rejects_full_ui_state(api_client: TestClient) -> None:
    response = api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "stage": "matching",
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            }
        },
    )

    assert response.status_code == 422


def test_meridian_api_returns_canonical_failure_suggestions(
    api_client: TestClient, monkeypatch
) -> None:
    engine = async_engine()
    engine.meridian.return_value = AgentExecution(
        response={
            "status": "BUDGET_FAIL",
            "message": "The budget does not support the current constraints.",
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {"awaiting": None}
                }
            },
            "options": [],
            "constraint_adjustment_suggestions": ["Increase the stay budget."],
            "relaxation_suggestions": ["Legacy field must not leak."],
        },
        prompt_release=PromptRelease("meridian", "1.0.0", "prompt"),
    )
    set_engine(api_client, engine)

    response = api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["constraint_adjustment_suggestions"] == [
        "Increase the stay budget."
    ]
    assert "relaxation_suggestions" not in body
    assert "version" not in body


def test_meridian_api_returns_typed_dynamic_recommendations(
    api_client: TestClient, monkeypatch
) -> None:
    engine = async_engine()
    option = recommendation_option()
    option["evaluations"][0]["details"] = [
        {
            "type": "cost_breakdown",
            "currency": "INR",
            "items": [
                {
                    "label": "Stay",
                    "per_person": {"minimum": 6000, "maximum": 7500},
                }
            ],
        }
    ]
    engine.meridian.return_value = AgentExecution(
        response={
            "status": "SUCCESS",
            "message": "The first option is the strongest overall fit.",
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {"awaiting": None}
                }
            },
            "trip_type": "single",
            "traveler_criteria": traveler_criteria(),
            "options": [option],
            "agent_meta": {"agent": "meridian", "prompt_version": "spoofed"},
        },
        prompt_release=PromptRelease("meridian", "2.0.0", "prompt"),
    )
    set_engine(api_client, engine)

    response = api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_meta"] == {
        "agent": "meridian",
        "prompt_version": "2.0.0",
    }
    assert body["traveler_criteria"] == traveler_criteria()
    cost = body["options"][0]["evaluations"][0]["details"][0]
    assert cost["currency"] == "INR"
    assert "per_person_total" not in cost
    assert "group_total" not in cost


def test_meridian_normalizer_rejects_legacy_recommendation_option() -> None:
    execution = AgentExecution(
        response={
            "status": "SUCCESS",
            "message": "Legacy output",
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {"awaiting": None}
                }
            },
            "options": [{"rank": 1, "name": "Legacy", "best_for": "Everything"}],
        },
        prompt_release=PromptRelease("meridian", "2.0.0", "prompt"),
    )

    with pytest.raises(ValidationError):
        _normalize_meridian_response(execution)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda output: output.pop("traveler_criteria"),
        lambda output: output["options"][0]["evaluations"].clear(),
        lambda output: output["options"][0]["evaluations"][0].update(
            {"criterion_id": "unknown"}
        ),
        lambda output: output["options"][0]["evaluations"].append(
            deepcopy(output["options"][0]["evaluations"][0])
        ),
    ],
)
def test_meridian_api_rejects_invalid_traveler_criteria_coverage(
    api_client: TestClient, monkeypatch, mutate
) -> None:
    output = meridian_success_output()
    mutate(output)

    assert_meridian_api_rejects(api_client, monkeypatch, output)


def test_meridian_api_rejects_duplicate_source_paths(
    api_client: TestClient, monkeypatch
) -> None:
    output = meridian_success_output()
    output["traveler_criteria"].append(
        {
            "id": "budget",
            "label": "Budget fit",
            "requirement_type": "PREFERENCE",
            "source_context_paths": ["travel_style.pace"],
        }
    )
    output["options"][0]["evaluations"].append(
        {
            "criterion_id": "budget",
            "outcome": "MATCH",
            "conclusion": "The estimate fits the stated budget.",
            "details": [{"type": "bullets", "items": ["Within range."]}],
        }
    )

    assert_meridian_api_rejects(api_client, monkeypatch, output)


def test_meridian_api_rejects_hard_requirement_mismatch(
    api_client: TestClient, monkeypatch
) -> None:
    output = meridian_success_output()
    output["traveler_criteria"][0]["requirement_type"] = "HARD"
    output["options"][0]["evaluations"][0].update(
        {
            "outcome": "MISMATCH",
            "tradeoffs": ["The option does not satisfy the hard requirement."],
        }
    )

    assert_meridian_api_rejects(api_client, monkeypatch, output)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda option: option.update({"verdict": "Legacy verdict"}),
        lambda option: option["evaluations"][0].update(
            {"details": [{"type": "note", "text": "Free-form note"}]}
        ),
        lambda option: option["evaluations"][0].update(
            {
                "details": [
                    {
                        "type": "cost_breakdown",
                        "currency": "INR",
                        "per_person_total": {"minimum": 5000, "maximum": 4000},
                    }
                ]
            }
        ),
    ],
)
def test_meridian_api_rejects_superseded_or_malformed_option_content(
    api_client: TestClient, monkeypatch, mutate
) -> None:
    output = meridian_success_output()
    mutate(output["options"][0])

    assert_meridian_api_rejects(api_client, monkeypatch, output)


def test_langgraph_preserves_normalized_scout_and_meridian_api_contracts(
    api_client: TestClient, monkeypatch
) -> None:
    model = FakeChatModel(
        [
            json.dumps(
                {
                    "message": "A mountain trip can work well.",
                    "state_delta": {
                        "trip_context": {"destination_scope": "mountains"}
                    },
                    "intent": "advise",
                }
            ),
            json.dumps(
                {
                    "status": "NEEDS_CLARIFICATION",
                    "message": "What budget should I use?",
                    "state_delta": {
                        "matcher_state": {
                            "conversation_context": {
                                "last_meridian_message": "What budget should I use?",
                                "awaiting": "budget",
                            }
                        }
                    },
                    "options": [],
                }
            ),
        ]
    )
    set_engine(
        api_client,
        AgentExecutionService(
            LangGraphAgentAdapter(runtime=LangGraphRuntime(model=model)),
            logger_for_test(),
            "langgraph",
        ),
    )

    scout_response = api_client.post(
        "/scout",
        json={
            "trip_state": {
                "stage": "new",
                "trip_context": {},
                "advisor_state": {},
            },
            "message": "Tell me about mountain trips.",
        },
    )
    meridian_response = api_client.post(
        "/meridian",
        json={
            "trip_state": {
                "trip_context": {"destination_scope": "mountains"},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            },
            "message": "Find mountain options.",
        },
    )

    assert scout_response.status_code == 200
    assert scout_response.json() == {
        "message": "A mountain trip can work well.",
        "state_delta": {"trip_context": {"destination_scope": "mountains"}},
        "intent": "advise",
        "agent_meta": {"agent": "scout", "prompt_version": "1.7.0"},
    }
    assert meridian_response.status_code == 200
    assert meridian_response.json() == {
        "status": "NEEDS_CLARIFICATION",
        "state_delta": {
            "trip_context": {},
            "matcher_state": {
                "conversation_context": {
                    "last_meridian_message": "What budget should I use?",
                    "awaiting": "budget",
                }
            },
        },
        "message": "What budget should I use?",
        "options": [],
        "agent_meta": {"agent": "meridian", "prompt_version": "1.6.0"},
    }


def test_invalid_output_after_repair_returns_cors_enabled_502(
    api_client: TestClient,
) -> None:
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=[
            AgentInvocationResult(raw_output="not-json"),
            AgentInvocationResult(raw_output="still-not-json"),
        ]
    )
    set_engine(
        api_client,
        AgentExecutionService(adapter, logger_for_test(), "test-engine"),
    )

    response = api_client.post(
        "/scout",
        headers={"Origin": "https://ui.test"},
        json={"trip_state": {}, "message": "Help me plan."},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The travel assistant returned an invalid response."
    }
    assert response.headers["access-control-allow-origin"] == "https://ui.test"
    assert adapter.invoke.await_count == 2


def test_adapter_timeout_returns_cors_enabled_504(api_client: TestClient) -> None:
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=AgentAdapterTimeoutError("provider timed out")
    )
    set_engine(
        api_client,
        AgentExecutionService(adapter, logger_for_test(), "test-engine"),
    )

    response = api_client.post(
        "/scout",
        headers={"Origin": "https://ui.test"},
        json={"trip_state": {}, "message": "Help me plan."},
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "The travel assistant timed out."}
    assert response.headers["access-control-allow-origin"] == "https://ui.test"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/scout",
            {"trip_state": {}, "message": "x" * (MAX_MESSAGE_CHARACTERS + 1)},
        ),
        (
            "/scout",
            {"trip_state": {"values": list(range(MAX_CONTAINER_ITEMS + 1))}},
        ),
        (
            "/meridian",
            {
                "trip_state": {
                    "trip_context": {"large": "x" * MAX_PHASE_STATE_BYTES},
                    "advisor_state": {},
                    "matcher_state": {},
                }
            },
        ),
    ],
)
def test_agent_api_rejects_resource_abusive_input_without_invoking_engine(
    api_client: TestClient, monkeypatch, path: str, payload: dict
) -> None:
    engine = async_engine()
    set_engine(api_client, engine)

    response = api_client.post(path, json=payload)

    assert response.status_code == 422
    engine.scout.assert_not_called()
    engine.meridian.assert_not_called()


def test_scout_api_rejects_full_ui_state(api_client: TestClient) -> None:
    engine = async_engine()
    set_engine(api_client, engine)
    response = api_client.post(
        "/scout",
        json={
            "trip_state": {
                "stage": "matching",
                "trip_context": {},
                "advisor_state": {},
                "active_agent": "scout",
                "matcher_state": {},
            }
        },
    )

    assert response.status_code == 422
    engine.scout.assert_not_called()


def test_agent_api_rejects_excessive_state_depth_without_invoking_engine(
    api_client: TestClient, monkeypatch
) -> None:
    nested: dict = {}
    cursor = nested
    for _ in range(MAX_DATA_DEPTH + 1):
        cursor["next"] = {}
        cursor = cursor["next"]
    engine = async_engine()
    set_engine(api_client, engine)

    response = api_client.post(
        "/scout", json={"trip_state": nested, "message": "travel question"}
    )

    assert response.status_code == 422
    engine.scout.assert_not_called()
