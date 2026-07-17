"""Configuration-driven agent-engine selection."""

import logging

from ...shared.properties import property_loader
from .contracts import AgentEngine
from .langgraph import LangGraphAgentEngine
from .n8n import N8NAgentEngine


logger = logging.getLogger("uvicorn.error")


def get_agent_engine() -> AgentEngine:
    engine_name = property_loader.get_string_property_with_default(
        "agent_engine", "n8n"
    ).strip().lower()

    if engine_name == "n8n":
        engine: AgentEngine = N8NAgentEngine()
    elif engine_name == "langgraph":
        engine = LangGraphAgentEngine()
    else:
        raise ValueError(
            f"Unsupported AGENT_ENGINE: {engine_name or '<empty>'}. "
            "Expected n8n or langgraph."
        )

    logger.info("Selected agent engine: %s", engine_name)
    return engine
