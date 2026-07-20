"""Explicit Meridian graph assembly."""

from langgraph.graph import END, START, StateGraph

from ..nodes import InvokeModelNode
from ..runtime import LangGraphRuntime
from ..state import AgentGraphInput, AgentGraphOutput, AgentGraphState


def build_meridian_graph(runtime: LangGraphRuntime):
    """Build the single-node Meridian model invocation graph."""

    builder = StateGraph(
        AgentGraphState,
        input_schema=AgentGraphInput,
        output_schema=AgentGraphOutput,
    )
    builder.add_node("invoke_meridian", InvokeModelNode(runtime.model))
    builder.add_edge(START, "invoke_meridian")
    builder.add_edge("invoke_meridian", END)
    return runtime.compile_graph("meridian", builder)
