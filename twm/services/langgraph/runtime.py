"""Provider configuration and graph compilation for LangGraph agents."""

import logging
from typing import Any

from langchain_groq import ChatGroq

from ..agent_engine.settings import AgentEngineSettings


logger = logging.getLogger("uvicorn.error")


class LangGraphRuntime:
    """Own provider construction and compile injected graph builders."""

    def __init__(
        self,
        model: Any | None = None,
        settings: AgentEngineSettings | None = None,
    ) -> None:
        self.model = model or self._create_model(settings)

    def structured_model(self, output_schema: type) -> Any:
        return self.model.with_structured_output(
            output_schema,
            method="json_schema",
            include_raw=True,
            strict=False,
        )

    @staticmethod
    def compile_graph(graph_name: str, builder: Any) -> Any:
        graph = builder.compile()
        logger.info("Compiled stateless LangGraph agent: %s", graph_name)
        return graph

    @staticmethod
    def _create_model(settings: AgentEngineSettings | None) -> ChatGroq:
        settings = settings or AgentEngineSettings.load()
        if settings.engine != "langgraph" or not settings.groq_api_key:
            raise ValueError("LangGraph settings are required to construct its runtime")

        logger.info(
            "Configured LangGraph model runtime: model=%s timeout_seconds=%s",
            settings.langgraph_model,
            settings.langgraph_timeout_seconds,
        )
        return ChatGroq(
            model=settings.langgraph_model,
            api_key=settings.groq_api_key,
            temperature=settings.langgraph_temperature,
            timeout=float(settings.langgraph_timeout_seconds),
            max_retries=0,
        )
