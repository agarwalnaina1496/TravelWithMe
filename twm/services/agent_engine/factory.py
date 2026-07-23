"""Configuration-driven agent-engine selection."""

import httpx

from ...telemetry import TelemetryLogger
from .contracts import AgentAdapter, AgentEngine
from .langgraph import LangGraphAgentAdapter
from .n8n import N8NAgentAdapter
from .service import AgentExecutionService
from .settings import AgentEngineSettings

def get_agent_engine(
    settings: AgentEngineSettings,
    logger: TelemetryLogger,
    http_client: httpx.AsyncClient | None = None,
) -> AgentEngine:
    if settings.engine == "n8n":
        if http_client is None:
            raise ValueError("n8n requires an application-owned HTTP client")
        adapter: AgentAdapter = N8NAgentAdapter(settings, http_client)
    elif settings.engine == "langgraph":
        adapter = LangGraphAgentAdapter(settings=settings)
    else:
        raise ValueError(f"Unsupported AGENT_ENGINE: {settings.engine}")

    logger.info(
        "Selected agent engine",
        event="be.agent.engine.selected",
        source="application",
        engine=settings.engine,
    )
    engine: AgentEngine = AgentExecutionService(
        adapter,
        logger=logger,
        engine_name=settings.engine,
        generation=settings.generation_config,
    )
    return engine
