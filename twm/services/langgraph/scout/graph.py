"""Explicit Scout graph assembly."""

from langgraph.graph import END, START, StateGraph

from ..nodes import PrepareAgentInputNode
from ..runtime import LangGraphRuntime
from ..state import AgentGraphInput, AgentGraphOutput, AgentGraphState
from .models import ScoutModelOutput
from .nodes import InvokeScoutNode, ParseScoutOutputNode


def build_scout_graph(runtime: LangGraphRuntime):
    """Build START -> prepare -> invoke Scout -> parse -> END."""

    builder = StateGraph(
        AgentGraphState,
        input_schema=AgentGraphInput,
        output_schema=AgentGraphOutput,
    )
    builder.add_node("prepare_input", PrepareAgentInputNode())
    builder.add_node(
        "invoke_scout",
        InvokeScoutNode(runtime.structured_model(ScoutModelOutput)),
    )
    builder.add_node("parse_scout_output", ParseScoutOutputNode())
    builder.add_edge(START, "prepare_input")
    builder.add_edge("prepare_input", "invoke_scout")
    builder.add_edge("invoke_scout", "parse_scout_output")
    builder.add_edge("parse_scout_output", END)
    return runtime.compile_graph("scout", builder)
