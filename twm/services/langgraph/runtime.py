"""Provider-neutral model configuration and graph compilation."""

import logging
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from ..agent_engine.settings import AgentEngineSettings


logger = logging.getLogger("uvicorn.error")


class LangGraphRuntime:
    """Own provider construction and compile injected graph builders."""

    def __init__(
        self,
        model: BaseChatModel | Any | None = None,
        settings: AgentEngineSettings | None = None,
    ) -> None:
        self.model = model or self._create_model(settings)

    @staticmethod
    def compile_graph(graph_name: str, builder: Any) -> Any:
        graph = builder.compile()
        logger.info("Compiled stateless LangGraph agent: %s", graph_name)
        return graph

    @staticmethod
    def _create_model(settings: AgentEngineSettings | None) -> BaseChatModel:
        settings = settings or AgentEngineSettings.load()
        if (
            settings.engine != "langgraph"
            or not settings.langgraph_model_provider
            or not settings.langgraph_api_key
        ):
            raise ValueError("LangGraph settings are required to construct its runtime")

        logger.info(
            "Configured LangGraph model runtime: provider=%s model=%s "
            "timeout_seconds=%s",
            settings.langgraph_model_provider,
            settings.langgraph_model,
            settings.langgraph_timeout_seconds,
        )
        return init_chat_model(
            model=settings.langgraph_model,
            model_provider=settings.langgraph_model_provider,
            api_key=settings.langgraph_api_key,
            temperature=settings.langgraph_temperature,
            timeout=float(settings.langgraph_timeout_seconds),
            max_retries=0,
        )
