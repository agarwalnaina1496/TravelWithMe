"""Configuration-driven agent-engine selection."""

import logging

import httpx

from .contracts import AgentAdapter, AgentEngine
from .langgraph import LangGraphAgentAdapter
from .n8n import N8NAgentAdapter
from .service import AgentExecutionService
from .settings import AgentEngineSettings
from ...telemetry import TelemetryLogger


logger = logging.getLogger("uvicorn.error")


def get_agent_engine(
    settings: AgentEngineSettings,
    http_client: httpx.AsyncClient | None = None,
    telemetry_logger: TelemetryLogger | None = None,
) -> AgentEngine:
    if settings.engine == "n8n":
        if http_client is None:
            raise ValueError("n8n requires an application-owned HTTP client")
        adapter: AgentAdapter = N8NAgentAdapter(settings, http_client)
    elif settings.engine == "langgraph":
        adapter = LangGraphAgentAdapter(settings=settings)
    else:
        raise ValueError(f"Unsupported AGENT_ENGINE: {settings.engine}")

    logger.info("Selected agent engine: %s", settings.engine)
    engine: AgentEngine = AgentExecutionService(adapter, telemetry_logger=telemetry_logger)
    return engine
