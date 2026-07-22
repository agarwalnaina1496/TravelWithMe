"""Configuration-driven adapter selection tests."""

from unittest.mock import Mock

import pytest

from twm.services import (
    AgentEngineSettings,
    AgentExecutionService,
    LangGraphAgentAdapter,
    N8NAgentAdapter,
)
from twm.services.agent_engine import factory
from twm.shared.properties import property_loader


def test_factory_wraps_exact_configured_adapter(monkeypatch) -> None:
    client = Mock()
    telemetry = Mock()
    n8n_settings = AgentEngineSettings(engine="n8n", environment="test")
    engine = factory.get_agent_engine(n8n_settings, telemetry, client)
    assert isinstance(engine, AgentExecutionService)
    assert isinstance(engine._adapter, N8NAgentAdapter)
    assert engine._telemetry is telemetry
    assert engine._engine_name == "n8n"

    sentinel = Mock(spec=LangGraphAgentAdapter)
    monkeypatch.setattr(
        factory, "LangGraphAgentAdapter", lambda settings: sentinel
    )
    langgraph_settings = AgentEngineSettings(
        engine="langgraph",
        environment="test",
        langgraph_model_provider="groq",
        langgraph_api_key="test",
    )
    engine = factory.get_agent_engine(langgraph_settings, telemetry, client)
    assert isinstance(engine, AgentExecutionService)
    assert engine._adapter is sentinel


def test_unknown_engine_is_rejected() -> None:
    settings = AgentEngineSettings(engine="shadow", environment="test")
    with pytest.raises(ValueError, match="Unsupported AGENT_ENGINE: shadow"):
        factory.get_agent_engine(settings, Mock(), Mock())


def test_committed_default_is_n8n() -> None:
    assert property_loader.CONFIG.get("APP", "agent_engine") == "n8n"
