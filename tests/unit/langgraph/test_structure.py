"""Graph topology and shared input-node tests."""

import json

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from twm.services import LangGraphAgentEngine, LangGraphRuntime
from twm.services.agent_engine import langgraph as engine_module
from twm.services.langgraph import build_meridian_graph, build_scout_graph
from twm.security import UNTRUSTED_DATA_PREAMBLE

from .fakes import FakeChatModel, prompt_release


@pytest.mark.parametrize(
    ("build_graph", "expected_nodes"),
    [
        (build_scout_graph, {"prepare_input", "invoke_scout", "parse_scout_output"}),
        (
            build_meridian_graph,
            {"prepare_input", "invoke_meridian", "parse_meridian_output"},
        ),
    ],
)
def test_graphs_expose_explicit_orchestration_nodes(
    build_graph, expected_nodes: set[str]
) -> None:
    model = FakeChatModel({"ScoutModelOutput": [], "MeridianModelOutput": []})
    graph = build_graph(LangGraphRuntime(model=model))

    assert expected_nodes.issubset(graph.get_graph().nodes)


def test_prepare_node_uses_prompt_and_phase_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeChatModel(
        {
            "ScoutModelOutput": [
                {"message": "Answer", "state_delta": {}, "intent": "advise"}
            ],
            "MeridianModelOutput": [],
        }
    )
    monkeypatch.setattr(engine_module, "load_prompt_release", prompt_release)
    engine = LangGraphAgentEngine(runtime=LangGraphRuntime(model=model))

    engine.scout({"trip_context": {"region": "Uttarakhand"}}, "Tell me more.")

    messages = model.calls["ScoutModelOutput"][0]
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "scout system prompt"
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content.startswith(UNTRUSTED_DATA_PREAMBLE)
    assert json.loads(messages[1].content.removeprefix(UNTRUSTED_DATA_PREAMBLE)) == {
        "trip_state": {"trip_context": {"region": "Uttarakhand"}},
        "message": "Tell me more.",
    }
