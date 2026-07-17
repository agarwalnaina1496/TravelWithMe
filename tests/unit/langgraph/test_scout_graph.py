"""Scout graph behavior and n8n parity tests."""

from typing import Any
from unittest.mock import Mock

import pytest

from twm.services import N8NAgentEngine
from twm.services.agent_engine import langgraph as langgraph_module
from twm.services.agent_engine import n8n as n8n_module
from twm.services.response_normalization import _normalize_scout_response

from .fakes import make_langgraph_engine, prompt_release


@pytest.mark.parametrize(
    "output",
    [
        {
            "message": "A mountain trip can work well.",
            "state_delta": {"trip_context": {"region": "Uttarakhand"}},
            "intent": "advise",
        },
        {"message": "Which month?", "state_delta": {}, "intent": "advise"},
        {
            "message": "I can send this to matching.",
            "state_delta": {"trip_context": {"destination_scope": "mountains"}},
            "intent": "matcher",
        },
        {
            "message": "Your itinerary can be planned next.",
            "state_delta": {"trip_context": {"trip_shape": "single"}},
            "intent": "planner",
        },
    ],
)
def test_scout_normalized_contract_matches_n8n(
    monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]
) -> None:
    monkeypatch.setattr(n8n_module, "load_prompt_release", prompt_release)
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    n8n = N8NAgentEngine()
    monkeypatch.setattr(n8n, "_forward", Mock(return_value=output))
    langgraph = make_langgraph_engine({"ScoutModelOutput": [output]})
    state = {"stage": "advising", "trip_context": {}}

    assert _normalize_scout_response(
        langgraph.scout(state, "Help me choose.")
    ) == _normalize_scout_response(n8n.scout(state, "Help me choose."))


def test_scout_malformed_output_uses_failure_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    engine = make_langgraph_engine(
        {
            "ScoutModelOutput": [
                {"raw": None, "parsed": None, "parsing_error": ValueError("invalid")}
            ]
        }
    )

    response = engine.scout({}, "hello").response

    assert response["state_delta"] == {}
    assert response["intent"] is None


def test_scout_provider_error_propagates_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    engine = make_langgraph_engine(
        {"ScoutModelOutput": [RuntimeError("provider unavailable")]}
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        engine.scout({}, "hello")


def test_scout_rejects_ui_owned_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(langgraph_module, "load_prompt_release", prompt_release)
    engine = make_langgraph_engine(
        {
            "ScoutModelOutput": [
                {
                    "message": "Invalid",
                    "state_delta": {
                        "trip_context": {"selected_option": {"id": "ui-owned"}}
                    },
                    "intent": "advise",
                }
            ]
        }
    )

    response = engine.scout({}, "select this").response
    assert response["state_delta"] == {}
    assert response["intent"] is None
