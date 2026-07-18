"""Configuration-driven agent-engine selection."""

import logging

import httpx

from .contracts import AgentEngine
from .langgraph import LangGraphAgentEngine
from .n8n import N8NAgentEngine
from .settings import AgentEngineSettings


logger = logging.getLogger("uvicorn.error")


def get_agent_engine(
    settings: AgentEngineSettings,
    http_client: httpx.AsyncClient | None = None,
) -> AgentEngine:
    if settings.engine == "n8n":
        if http_client is None:
            raise ValueError("n8n requires an application-owned HTTP client")
        engine: AgentEngine = N8NAgentEngine(settings, http_client)
    elif settings.engine == "langgraph":
        engine = LangGraphAgentEngine(settings=settings)
    else:
        raise ValueError(f"Unsupported AGENT_ENGINE: {settings.engine}")

    logger.info("Selected agent engine: %s", settings.engine)
    return engine
