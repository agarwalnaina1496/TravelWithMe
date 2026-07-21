"""Single-node LangGraph topology and adapter tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from twm.services import (
    AgentAdapterError,
    AgentAdapterTimeoutError,
    AgentEngineSettings,
    AgentInvocation,
    LangGraphAgentAdapter,
    LangGraphRuntime,
)
from twm.services.langgraph import build_meridian_graph, build_scout_graph

from .fakes import FakeChatModel


@pytest.mark.parametrize(
    ("build_graph", "invocation_node"),
    [
        (build_scout_graph, "invoke_scout"),
        (build_meridian_graph, "invoke_meridian"),
    ],
)
def test_graphs_contain_exactly_one_application_node(
    build_graph, invocation_node: str
) -> None:
    graph = build_graph(LangGraphRuntime(model=FakeChatModel([])))
    application_nodes = set(graph.get_graph().nodes) - {"__start__", "__end__"}

    assert application_nodes == {invocation_node}


def test_langgraph_adapter_returns_raw_content_for_both_agents() -> None:
    model = FakeChatModel(['{"message":"scout"}', '{"message":"meridian"}'])
    adapter = LangGraphAgentAdapter(runtime=LangGraphRuntime(model=model))

    scout = asyncio.run(
        adapter.invoke("scout", AgentInvocation("scout system", "scout user"))
    )
    meridian = asyncio.run(
        adapter.invoke(
            "meridian", AgentInvocation("meridian system", "meridian user")
        )
    )

    assert scout.raw_output == '{"message":"scout"}'
    assert meridian.raw_output == '{"message":"meridian"}'
    assert isinstance(model.calls[0][0], SystemMessage)
    assert model.calls[0][0].content == "scout system"
    assert isinstance(model.calls[0][1], HumanMessage)
    assert model.calls[0][1].content == "scout user"


def test_langgraph_adapter_preserves_provider_telemetry() -> None:
    response = AIMessage(
        content='{"message":"scout"}',
        response_metadata={
            "finish_reason": "stop",
            "token_usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
                "queue_time": 0.0025,
                "prompt_time": 0.005,
                "completion_time": 0.04,
                "total_time": 0.0475,
            },
        },
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "output_token_details": {"reasoning": 8},
        },
    )
    adapter = LangGraphAgentAdapter(
        runtime=LangGraphRuntime(model=FakeChatModel([response]))
    )

    result = asyncio.run(
        adapter.invoke("scout", AgentInvocation("system", "user"))
    )

    assert result.metadata == {
        "finish_reason": "stop",
        "input_tokens": 120,
        "output_tokens": 40,
        "total_tokens": 160,
        "reasoning_tokens": 8,
        "queue_time_ms": 2.5,
        "model_time_ms": 45.0,
        "provider_total_time_ms": 47.5,
        "provider_attempts": 1,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("provider unavailable"), AgentAdapterError),
        (TimeoutError("slow"), AgentAdapterTimeoutError),
        (
            type("ProviderTimeoutError", (Exception,), {})("slow"),
            AgentAdapterTimeoutError,
        ),
    ],
)
def test_langgraph_adapter_maps_provider_failures(error, expected) -> None:
    adapter = LangGraphAgentAdapter(
        runtime=LangGraphRuntime(model=FakeChatModel([error]))
    )

    with pytest.raises(expected):
        asyncio.run(
            adapter.invoke("scout", AgentInvocation("system", "user"))
        )


@pytest.mark.parametrize("result", [None, [], "raw output"])
def test_langgraph_adapter_rejects_non_mapping_graph_result(result) -> None:
    adapter = LangGraphAgentAdapter(
        runtime=LangGraphRuntime(model=FakeChatModel([]))
    )
    adapter._scout_graph = AsyncMock()
    adapter._scout_graph.ainvoke.return_value = result

    with pytest.raises(AgentAdapterError):
        asyncio.run(
            adapter.invoke("scout", AgentInvocation("system", "user"))
        )


def test_langgraph_adapter_construction_uses_generic_runtime_settings(
    monkeypatch,
) -> None:
    model = FakeChatModel([])
    runtime = LangGraphRuntime(model=model)
    monkeypatch.setattr(
        "twm.services.agent_engine.langgraph.LangGraphRuntime",
        lambda settings: runtime,
    )
    settings = AgentEngineSettings(
        engine="langgraph",
        environment="test",
        langgraph_model_provider="groq",
        langgraph_api_key="secret",
    )

    adapter = LangGraphAgentAdapter(settings=settings)

    assert adapter._scout_graph is not None
    assert adapter._meridian_graph is not None
