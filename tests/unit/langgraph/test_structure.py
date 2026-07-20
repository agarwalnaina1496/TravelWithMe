"""Single-node LangGraph topology and adapter tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

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

    assert scout == '{"message":"scout"}'
    assert meridian == '{"message":"meridian"}'
    assert isinstance(model.calls[0][0], SystemMessage)
    assert model.calls[0][0].content == "scout system"
    assert isinstance(model.calls[0][1], HumanMessage)
    assert model.calls[0][1].content == "scout user"


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
