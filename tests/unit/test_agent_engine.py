"""Unit coverage for the TWM-54 LangGraph runtime foundation."""

from typing import Any, TypedDict
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from twm.services import LangGraphRuntime, N8NAgentEngine
from twm.services import agent_engine, langgraph_runtime


class GraphState(TypedDict):
    value: int


class StructuredOutput(BaseModel):
    message: str


def test_runtime_compiles_a_stateless_single_node_graph() -> None:
    graph = LangGraphRuntime.compile_single_node_graph(
        GraphState,
        "increment",
        lambda state: {"value": state["value"] + 1},
    )

    assert graph.invoke({"value": 4}) == {"value": 5}
    assert graph.invoke({"value": 4}) == {"value": 5}


def test_runtime_prepares_provider_structured_output() -> None:
    model = Mock()
    structured = object()
    model.with_structured_output.return_value = structured
    runtime = LangGraphRuntime(model=model)

    assert runtime.structured_model(StructuredOutput) is structured
    model.with_structured_output.assert_called_once_with(
        StructuredOutput,
        method="json_schema",
        include_raw=True,
        strict=False,
    )


def test_n8n_remains_the_committed_default() -> None:
    assert agent_engine.property_loader.CONFIG.get("APP", "agent_engine") == "n8n"
    assert isinstance(agent_engine.get_agent_engine(), N8NAgentEngine)


def test_langgraph_selection_is_reserved_for_twm_56(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_engine.property_loader,
        "get_string_property_with_default",
        lambda key, default: "langgraph",
    )

    with pytest.raises(ValueError, match="reserved for TWM-56"):
        agent_engine.get_agent_engine()


def test_unknown_engine_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_engine.property_loader,
        "get_string_property_with_default",
        lambda key, default: "shadow",
    )

    with pytest.raises(ValueError, match="Unsupported AGENT_ENGINE: shadow"):
        agent_engine.get_agent_engine()


def test_runtime_requires_credentials_only_when_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(key: str) -> str:
        raise KeyError(key)

    monkeypatch.setattr(
        langgraph_runtime.property_loader,
        "get_string_property",
        missing,
    )

    with pytest.raises(ValueError, match="GROQ_API_KEY is required"):
        LangGraphRuntime()


@pytest.mark.parametrize(
    ("timeout", "temperature", "error"),
    [
        (0, "0.7", "LANGGRAPH_TIMEOUT_SECONDS must be a positive integer"),
        (60, "invalid", "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"),
        (60, "2.1", "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"),
    ],
)
def test_runtime_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
    timeout: int,
    temperature: str,
    error: str,
) -> None:
    monkeypatch.setattr(
        langgraph_runtime.property_loader,
        "get_string_property",
        lambda key: "test-key",
    )
    monkeypatch.setattr(
        langgraph_runtime.property_loader,
        "get_int_property_with_default",
        lambda key, default: timeout,
    )
    monkeypatch.setattr(
        langgraph_runtime.property_loader,
        "get_string_property_with_default",
        lambda key, default: (
            temperature if key == "langgraph_temperature" else default
        ),
    )

    with pytest.raises(ValueError, match=error):
        LangGraphRuntime()
