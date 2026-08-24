"""AgentEngineSettings.load() configuration-boundary tests.

PR-review fix (TWM-195): the internal Trusted Actions route-mode classifier
always uses the LangGraph/LLM path directly, regardless of which engine is
primary (there is no deployed n8n classifier workflow). So
``langgraph_api_key`` (and provider/model, which have defaults) must always
be loaded/required, even when ``agent_engine=n8n`` -- not only inside the
``engine == "langgraph"`` branch.
"""

import os

import pytest

from twm.services.agent_engine.settings import AgentEngineSettings


def _n8n_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "n8n")
    monkeypatch.setenv("N8N_SCOUT_WEBHOOK_URL", "https://agents.test/webhook/scout")
    monkeypatch.setenv("N8N_MERIDIAN_WEBHOOK_URL", "https://agents.test/webhook/meridian")
    monkeypatch.setenv("N8N_GUIDE_WEBHOOK_URL", "https://agents.test/webhook/guide")
    monkeypatch.setenv("N8N_ATLAS_WEBHOOK_URL", "https://agents.test/webhook/atlas")
    monkeypatch.setenv("N8N_TIMEOUT_SECONDS", "185")
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "180")


def test_n8n_engine_requires_langgraph_api_key(monkeypatch) -> None:
    _n8n_env(monkeypatch)
    monkeypatch.delenv("LANGGRAPH_API_KEY", raising=False)

    with pytest.raises(ValueError, match="LANGGRAPH_API_KEY"):
        AgentEngineSettings.load()


def test_n8n_engine_succeeds_with_langgraph_api_key_supplied(monkeypatch) -> None:
    _n8n_env(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_API_KEY", "test-langgraph-key")

    settings = AgentEngineSettings.load()

    assert settings.engine == "n8n"
    assert settings.langgraph_api_key == "test-langgraph-key"
    # Defaults still apply when not otherwise configured.
    assert settings.langgraph_model_provider == "groq"
    assert settings.langgraph_model == "openai/gpt-oss-120b"
    # The removed n8n-only classifier webhook field no longer exists.
    assert not hasattr(settings, "n8n_route_classifier_webhook_url")
