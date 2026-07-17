"""API tests for the Scout and Meridian router."""

from copy import deepcopy
from unittest.mock import Mock

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
from twm.schemas import MeridianAgentOutput, ScoutAgentOutput
from twm.services import (
    AgentExecution,
    LangGraphAgentEngine,
    LangGraphRuntime,
    N8NAgentEngine,
)
from twm.services.response_normalization import _normalize_meridian_response
from twm.schemas.scout import ScoutResponse
from tests.factories import recommendation_option, traveler_criteria


def fake_langgraph_model(outputs: dict[str, dict]) -> Mock:
    model = Mock()

    def with_structured_output(schema: type, **kwargs):
        runnable = Mock()
        runnable.invoke.return_value = {
            "raw": None,
            "parsed": schema.model_validate(outputs[schema.__name__]),
            "parsing_error": None,
        }
        return runnable

    model.with_structured_output.side_effect = with_structured_output
    return model


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
    engine = Mock()
    engine.meridian.return_value = AgentExecution(
        response=output,
        prompt_release=PromptRelease("meridian", "2.0.0", "prompt"),
    )
    monkeypatch.setattr(trip_matcher, "engine", engine)

    with pytest.raises(ValidationError):
        api_client.post(
            "/meridian",
            json={
                "trip_state": {
                    "trip_context": {},
                    "advisor_state": {"conversation_context": {}},
                    "matcher_state": {},
                }
            },
        )


def test_active_phase_prompt_releases_are_complete() -> None:
    validate_prompt_release_files()

    assert load_prompt_versions() == {
        "scout": "1.5.0",
        "meridian": "1.3.0",
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
    engine = Mock()
    engine.scout.return_value = AgentExecution(
        response={
            "message": "Here is a broad answer.",
            "state_delta": {"trip_context": {"region": "Uttarakhand"}},
            "intent": "advise",
        },
        prompt_release=PromptRelease("scout", "1.1.0", "prompt"),
    )
    monkeypatch.setattr(trip_matcher, "engine", engine)
    payload = {
        "trip_state": {
            "stage": "new",
            "trip_context": {},
            "advisor_state": {},
        },
        "message": "Tell me about Uttarakhand.",
    }

    response = api_client.post("/scout", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "message": "Here is a broad answer.",
        "state_delta": {"trip_context": {"region": "Uttarakhand"}},
        "intent": "advise",
        "agent_meta": {"agent": "scout", "prompt_version": "1.1.0"},
    }
    engine.scout.assert_called_once_with(
        payload["trip_state"], "Tell me about Uttarakhand."
    )


def test_scout_api_forwards_backend_output_schema(
    api_client: TestClient, monkeypatch
) -> None:
    engine = N8NAgentEngine()
    forward = Mock(
        return_value={
            "message": "A mountain trip can work well.",
            "state_delta": {"trip_context": {"region": "Uttarakhand"}},
            "intent": "advise",
        }
    )
    monkeypatch.setattr(engine, "_forward", forward)
    monkeypatch.setattr(trip_matcher, "engine", engine)
    payload = {
        "trip_state": {"stage": "new", "trip_context": {}},
        "message": "Tell me about Uttarakhand.",
    }

    response = api_client.post("/scout", json=payload)

    assert response.status_code == 200
    release = load_prompt_release("scout")
    forward.assert_called_once_with(
        "scout",
        "n8n_scout_webhook_url",
        {
            "prompt": release.content,
            "trip_state": payload["trip_state"],
            "message": "Tell me about Uttarakhand.",
            "output_schema": ScoutAgentOutput.model_json_schema(),
        },
    )


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
    engine = Mock()
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
    monkeypatch.setattr(trip_matcher, "engine", engine)
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
    engine = N8NAgentEngine()
    forward = Mock(
        return_value={
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
    monkeypatch.setattr(engine, "_forward", forward)
    monkeypatch.setattr(trip_matcher, "engine", engine)
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
        "prompt_version": "1.3.0",
    }
    release = load_prompt_release("meridian")
    forward.assert_called_once_with(
        "meridian",
        "n8n_meridian_webhook_url",
        {
            "prompt": release.content,
            "trip_state": payload["trip_state"],
            "message": "Keep it mid-range.",
            "output_schema": MeridianAgentOutput.model_json_schema(),
        },
    )


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
    engine = Mock()
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
    monkeypatch.setattr(trip_matcher, "engine", engine)

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
    engine = Mock()
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
    monkeypatch.setattr(trip_matcher, "engine", engine)

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
    model = fake_langgraph_model(
        {
            "ScoutModelOutput": {
                "message": "A mountain trip can work well.",
                "state_delta": {"trip_context": {"destination_scope": "mountains"}},
                "intent": "advise",
            },
            "MeridianModelOutput": {
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
            },
        }
    )
    monkeypatch.setattr(
        trip_matcher,
        "engine",
        LangGraphAgentEngine(runtime=LangGraphRuntime(model=model)),
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
        "agent_meta": {"agent": "scout", "prompt_version": "1.5.0"},
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
        "agent_meta": {"agent": "meridian", "prompt_version": "1.3.0"},
    }
