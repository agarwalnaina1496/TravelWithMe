"""LangGraph adapter returning raw model completions."""

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..langgraph import LangGraphRuntime, build_meridian_graph, build_scout_graph
from .contracts import (
    AgentAdapterError,
    AgentAdapterTimeoutError,
    AgentInvocation,
    AgentInvocationResult,
    AgentName,
)
from .settings import AgentEngineSettings


class LangGraphAgentAdapter:
    """Invoke Scout and Meridian through independent single-node graphs."""

    def __init__(
        self,
        runtime: LangGraphRuntime | None = None,
        settings: AgentEngineSettings | None = None,
    ) -> None:
        runtime = runtime or LangGraphRuntime(settings=settings)
        self._settings = settings
        self._scout_graph = build_scout_graph(runtime)
        self._meridian_graph = build_meridian_graph(runtime)

    @property
    def engine_name(self) -> str:
        return "langgraph"

    def endpoint(self, agent: AgentName) -> str | None:
        provider = self._settings.langgraph_model_provider
        model = self._settings.langgraph_model
        if provider:
            return f"langgraph:{provider}/{model}"
        return f"langgraph:{model}"

    async def invoke(
        self, agent: AgentName, invocation: AgentInvocation
    ) -> AgentInvocationResult:
        graphs = {
            "scout": self._scout_graph,
            "meridian": self._meridian_graph,
        }
        try:
            result = await graphs[agent].ainvoke(
                {
                    "messages": [
                        SystemMessage(content=invocation.system_prompt),
                        HumanMessage(content=invocation.user_prompt),
                    ]
                }
            )
        except Exception as error:
            if _is_timeout_error(error):
                raise AgentAdapterTimeoutError(
                    f"{agent} LangGraph invocation timed out"
                ) from error
            raise AgentAdapterError(
                f"{agent} LangGraph invocation failed"
            ) from error

        if not isinstance(result, Mapping):
            raise AgentAdapterError(
                f"{agent} LangGraph response was not a mapping"
            )
        raw_output = result.get("raw_output")
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise AgentAdapterError(
                f"{agent} LangGraph response did not contain raw_output"
            )
        metadata = result.get("provider_metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        return AgentInvocationResult(
            raw_output=raw_output,
            metadata={
                key: value
                for key, value in metadata.items()
                if isinstance(key, str)
                and isinstance(value, (str, int, float))
                and not isinstance(value, bool)
            },
        )


def _is_timeout_error(error: BaseException) -> bool:
    """Recognize timeouts without coupling the adapter to one provider SDK."""

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, TimeoutError) or type(current).__name__.endswith(
            "TimeoutError"
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
