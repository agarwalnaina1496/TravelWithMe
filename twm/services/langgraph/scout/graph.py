"""Explicit Scout graph assembly."""

from langgraph.graph import END, START, StateGraph

from ..nodes import InvokeModelNode
from ..runtime import LangGraphRuntime
from ..state import AgentGraphInput, AgentGraphOutput, AgentGraphState


def build_scout_graph(runtime: LangGraphRuntime):
    """Build the single-node Scout model invocation graph."""

    builder = StateGraph(
        AgentGraphState,
        input_schema=AgentGraphInput,
        output_schema=AgentGraphOutput,
    )
    builder.add_node("invoke_scout", InvokeModelNode(runtime.model))
    builder.add_edge(START, "invoke_scout")
    builder.add_edge("invoke_scout", END)
    return runtime.compile_graph("scout", builder)
