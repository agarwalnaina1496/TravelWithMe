"""Reusable configuration and stateless graph wiring for LangGraph agents."""

import logging
from typing import Any, Callable

from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph

from ..shared.properties import property_loader


logger = logging.getLogger("uvicorn.error")


class LangGraphRuntime:
    """Construct the shared model and compile stateless single-node graphs."""

    def __init__(self, model: Any | None = None) -> None:
        self.model = model or self._create_model()

    def structured_model(self, output_schema: type) -> Any:
        return self.model.with_structured_output(
            output_schema,
            method="json_schema",
            include_raw=True,
            strict=False,
        )

    @staticmethod
    def compile_single_node_graph(
        state_schema: type,
        node_name: str,
        node: Callable[[Any], dict[str, Any]],
        *,
        input_schema: type | None = None,
        output_schema: type | None = None,
    ) -> Any:
        builder = StateGraph(
            state_schema,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        builder.add_node(node_name, node)
        builder.add_edge(START, node_name)
        builder.add_edge(node_name, END)
        graph = builder.compile()
        logger.info("Compiled stateless LangGraph runtime node: %s", node_name)
        return graph

    @staticmethod
    def _create_model() -> ChatGroq:
        api_key = LangGraphRuntime._required_property("groq_api_key")
        model_name = property_loader.get_string_property_with_default(
            "langgraph_model", "openai/gpt-oss-120b"
        ).strip()
        if not model_name:
            raise ValueError("LANGGRAPH_MODEL is required for the LangGraph runtime")

        try:
            timeout_seconds = property_loader.get_int_property_with_default(
                "langgraph_timeout_seconds", 60
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "LANGGRAPH_TIMEOUT_SECONDS must be a positive integer"
            ) from exc
        if timeout_seconds <= 0:
            raise ValueError("LANGGRAPH_TIMEOUT_SECONDS must be a positive integer")

        try:
            temperature = float(
                property_loader.get_string_property_with_default(
                    "langgraph_temperature", "0.7"
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"
            ) from exc
        if not 0 <= temperature <= 2:
            raise ValueError(
                "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"
            )

        logger.info(
            "Configured LangGraph model runtime: model=%s timeout_seconds=%s",
            model_name,
            timeout_seconds,
        )
        return ChatGroq(
            model=model_name,
            api_key=api_key,
            temperature=temperature,
            timeout=float(timeout_seconds),
            max_retries=0,
        )

    @staticmethod
    def _required_property(key: str) -> str:
        try:
            value = property_loader.get_string_property(key).strip()
        except Exception as exc:
            raise ValueError(
                "GROQ_API_KEY is required to construct the LangGraph runtime"
            ) from exc
        if not value:
            raise ValueError(
                "GROQ_API_KEY is required to construct the LangGraph runtime"
            )
        return value
