"""Configuration-driven engine selection tests."""

from unittest.mock import Mock

import pytest

from twm.services import AgentEngineSettings, N8NAgentEngine
from twm.services.agent_engine import factory
from twm.shared.properties import property_loader


def test_factory_selects_exact_configured_engine(monkeypatch) -> None:
    client = Mock()
    n8n_settings = AgentEngineSettings(engine="n8n", environment="test")
    assert isinstance(
        factory.get_agent_engine(n8n_settings, client), N8NAgentEngine
    )

    sentinel = object()
    monkeypatch.setattr(
        factory, "LangGraphAgentEngine", lambda settings: sentinel
    )
    langgraph_settings = AgentEngineSettings(
        engine="langgraph", environment="test", groq_api_key="test"
    )
    assert factory.get_agent_engine(langgraph_settings, client) is sentinel


def test_unknown_engine_is_rejected() -> None:
    settings = AgentEngineSettings(engine="shadow", environment="test")
    with pytest.raises(ValueError, match="Unsupported AGENT_ENGINE: shadow"):
        factory.get_agent_engine(settings, Mock())


def test_committed_default_is_n8n() -> None:
    assert property_loader.CONFIG.get("APP", "agent_engine") == "n8n"
