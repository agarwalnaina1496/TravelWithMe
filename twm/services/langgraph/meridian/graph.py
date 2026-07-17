"""Explicit Meridian graph assembly."""

from langgraph.graph import END, START, StateGraph

from ..nodes import PrepareAgentInputNode
from ..runtime import LangGraphRuntime
from ..state import AgentGraphInput, AgentGraphOutput, AgentGraphState
from .models import MeridianModelOutput
from .nodes import InvokeMeridianNode, ParseMeridianOutputNode


def build_meridian_graph(runtime: LangGraphRuntime):
    """Build START -> prepare -> invoke Meridian -> parse -> END."""

    builder = StateGraph(
        AgentGraphState,
        input_schema=AgentGraphInput,
        output_schema=AgentGraphOutput,
    )
    builder.add_node("prepare_input", PrepareAgentInputNode())
    builder.add_node(
        "invoke_meridian",
        InvokeMeridianNode(runtime.structured_model(MeridianModelOutput)),
    )
    builder.add_node("parse_meridian_output", ParseMeridianOutputNode())
    builder.add_edge(START, "prepare_input")
    builder.add_edge("prepare_input", "invoke_meridian")
    builder.add_edge("invoke_meridian", "parse_meridian_output")
    builder.add_edge("parse_meridian_output", END)
    return runtime.compile_graph("meridian", builder)
