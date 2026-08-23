"""Generic single-turn raw-invocation graph.

Structurally identical to ``scout``/``meridian``'s single-node graph
(``InvokeModelNode``), but not tied to any ``AgentName`` — used by
``LangGraphAgentAdapter.invoke_raw`` for small internal callers (e.g. the
Trusted Actions route-mode classifier, TWM-195) that need one raw
system/user prompt turn without the trip_state-shaped scout/meridian/guide/
atlas dispatch.
"""

from langgraph.graph import END, START, StateGraph

from ..nodes import InvokeModelNode
from ..runtime import LangGraphRuntime
from ..state import AgentGraphInput, AgentGraphOutput, AgentGraphState


def build_raw_invocation_graph(runtime: LangGraphRuntime):
    """Build the single-node generic raw-invocation graph."""

    builder = StateGraph(
        AgentGraphState,
        input_schema=AgentGraphInput,
        output_schema=AgentGraphOutput,
    )
    builder.add_node("invoke_raw", InvokeModelNode(runtime.model))
    builder.add_edge(START, "invoke_raw")
    builder.add_edge("invoke_raw", END)
    return runtime.compile_graph("raw", builder)
