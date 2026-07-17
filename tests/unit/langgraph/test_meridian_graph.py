"""Meridian graph behavior and n8n parity tests."""

from typing import Any
from unittest.mock import Mock

import pytest

from twm.services import N8NAgentEngine
from twm.services.agent_engine import langgraph as langgraph_module
from twm.services.agent_engine import n8n as n8n_module
from twm.services.response_normalization import _normalize_meridian_response
from tests.factories import recommendation_criteria_catalog, recommendation_option

from .fakes import make_langgraph_engine, prompt_release


@pytest.mark.parametrize(
    "output",
    [
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
        },
        {
            "status": "SUCCESS",
            "message": "I found two suitable options.",
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {"awaiting": None}
                }
            },
            "trip_type": "single",
            "criteria_catalog": recommendation_criteria_catalog(),
            "options": [recommendation_option(1), recommendation_option(2)],
        },
        {
            "status": "BUDGET_FAIL",
            "message": "The current budget is too low.",
            "state_delta": {
                "matcher_state": {
                    "conversation_context": {"awaiting": None}
                }
            },
            "options": [],
            "constraint_adjustment_suggestions": ["Increase the stay budget."],
        },
    ],
)
def test_meridian_normalized_contract_matches_n8n(
    monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]
) -> None:
    monkeypatch.setattr(n8n_module, "load_prompt_release", prompt_release)
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    n8n = N8NAgentEngine()
    monkeypatch.setattr(n8n, "_forward", Mock(return_value=output))
    langgraph = make_langgraph_engine({"MeridianModelOutput": [output]})
    state = {"trip_context": {"destination_scope": "mountains"}}

    assert _normalize_meridian_response(
        langgraph.meridian(state, "Find options.")
    ) == _normalize_meridian_response(n8n.meridian(state, "Find options."))


def test_meridian_malformed_output_uses_failure_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    engine = make_langgraph_engine(
        {
            "MeridianModelOutput": [
                {"raw": None, "parsed": None, "parsing_error": ValueError("invalid")}
            ]
        }
    )

    response = engine.meridian({}, "hello").response

    assert response["status"] == "HARD_FAIL"
    assert response["state_delta"] == {
        "matcher_state": {"conversation_context": {"awaiting": None}}
    }
    assert response["options"] == []


def test_meridian_rejects_ui_owned_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    engine = make_langgraph_engine(
        {
            "MeridianModelOutput": [
                {
                    "status": "SUCCESS",
                    "message": "Invalid",
                    "state_delta": {
                        "matcher_state": {"recommendations": [{"id": "ui-owned"}]}
                    },
                    "options": [],
                }
            ]
        }
    )

    response = engine.meridian({}, "select this").response
    assert response["status"] == "HARD_FAIL"
    assert response["state_delta"] == {
        "matcher_state": {"conversation_context": {"awaiting": None}}
    }
    assert response["options"] == []
