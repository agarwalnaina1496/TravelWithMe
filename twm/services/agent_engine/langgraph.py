"""LangGraph implementation of the agent-engine contract."""

from typing import Any, Optional

from ...prompts import load_prompt_release
from ..langgraph import LangGraphRuntime, build_meridian_graph, build_scout_graph
from .contracts import AgentExecution


class LangGraphAgentEngine:
    """Execute Scout and Meridian through independent compiled graphs."""

    def __init__(self, runtime: LangGraphRuntime | None = None) -> None:
        runtime = runtime or LangGraphRuntime()
        self._scout_graph = build_scout_graph(runtime)
        self._meridian_graph = build_meridian_graph(runtime)

    def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return self._execute(self._scout_graph, "scout", trip_state, message)

    def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return self._execute(self._meridian_graph, "meridian", trip_state, message)

    @staticmethod
    def _execute(
        graph: Any,
        agent: str,
        trip_state: dict[str, Any],
        message: Optional[str],
    ) -> AgentExecution:
        release = load_prompt_release(agent)
        result = graph.invoke(
            {
                "prompt": release.content,
                "trip_state": trip_state,
                "message": message,
            }
        )
        return AgentExecution(
            response=result["response"],
            prompt_release=release,
        )
