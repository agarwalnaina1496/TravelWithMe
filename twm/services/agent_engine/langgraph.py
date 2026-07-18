"""LangGraph implementation of the agent-engine contract."""

from typing import Any, Optional

from ...prompts import load_prompt_release
from ..langgraph import LangGraphRuntime, build_meridian_graph, build_scout_graph
from .contracts import AgentExecution
from .settings import AgentEngineSettings


class LangGraphAgentEngine:
    """Execute Scout and Meridian through independent compiled graphs."""

    def __init__(
        self,
        runtime: LangGraphRuntime | None = None,
        settings: AgentEngineSettings | None = None,
    ) -> None:
        runtime = runtime or LangGraphRuntime(settings=settings)
        self._scout_graph = build_scout_graph(runtime)
        self._meridian_graph = build_meridian_graph(runtime)

    async def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return await self._execute(self._scout_graph, "scout", trip_state, message)

    async def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return await self._execute(
            self._meridian_graph, "meridian", trip_state, message
        )

    @staticmethod
    async def _execute(
        graph: Any,
        agent: str,
        trip_state: dict[str, Any],
        message: Optional[str],
    ) -> AgentExecution:
        release = load_prompt_release(agent)
        result = await graph.ainvoke(
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
