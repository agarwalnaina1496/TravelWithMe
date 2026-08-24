"""Configuration-driven agent-engine selection."""

import httpx

from ...telemetry import TelemetryLogger
from .contracts import AgentAdapter, AgentEngine
from .langgraph import LangGraphAgentAdapter
from .n8n import N8NAgentAdapter
from .service import AgentExecutionService
from .settings import AgentEngineSettings

def build_agent_adapter(
    settings: AgentEngineSettings,
    http_client: httpx.AsyncClient | None = None,
) -> AgentAdapter:
    """Construct the engine-selected adapter on its own.

    Split out from ``get_agent_engine`` so a caller that needs the raw
    ``AgentAdapter`` directly, without going through the trip_state/
    AgentName-shaped ``AgentExecutionService`` dispatch, can share the same
    engine-selected adapter instance instead of constructing a second one.
    (TWM-195 originally added this split for an internal route-mode
    classifier; that classifier was rejected on re-review and removed, but
    this split-adapter shape remains a reasonable general seam.)
    """

    if settings.engine == "n8n":
        if http_client is None:
            raise ValueError("n8n requires an application-owned HTTP client")
        return N8NAgentAdapter(settings, http_client)
    if settings.engine == "langgraph":
        return LangGraphAgentAdapter(settings=settings)
    raise ValueError(f"Unsupported AGENT_ENGINE: {settings.engine}")


def get_agent_engine(
    settings: AgentEngineSettings,
    logger: TelemetryLogger,
    http_client: httpx.AsyncClient | None = None,
    adapter: AgentAdapter | None = None,
) -> AgentEngine:
    adapter = adapter or build_agent_adapter(settings, http_client)

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
