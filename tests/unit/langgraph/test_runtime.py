"""Provider-neutral LangGraph runtime configuration tests."""

from unittest.mock import Mock

import pytest

from twm.services import AgentEngineSettings, LangGraphRuntime
from twm.services.agent_engine import settings as settings_module
from twm.services.langgraph import runtime as runtime_module


def test_runtime_initializes_configured_provider_without_provider_class(
    monkeypatch,
) -> None:
    model = Mock()
    initializer = Mock(return_value=model)
    monkeypatch.setattr(runtime_module, "init_chat_model", initializer)
    settings = AgentEngineSettings(
        engine="langgraph",
        environment="test",
        langgraph_model_provider="groq",
        langgraph_api_key="secret",
        langgraph_model="openai/gpt-oss-120b",
        langgraph_temperature=0.3,
        langgraph_timeout_seconds=45,
    )

    runtime = LangGraphRuntime(settings=settings)

    assert runtime.model is model
    initializer.assert_called_once_with(
        model="openai/gpt-oss-120b",
        model_provider="groq",
        api_key="secret",
        temperature=0.3,
        timeout=45.0,
        max_retries=0,
    )


def test_settings_validate_only_selected_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "n8n_scout_webhook_url": "https://agents.test/scout",
        "n8n_meridian_webhook_url": "https://agents.test/meridian",
    }
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_string_property_with_default",
        lambda key, default: "n8n" if key == "agent_engine" else default,
    )
    monkeypatch.setattr(
        settings_module.property_loader, "get_string_property", values.__getitem__
    )
    monkeypatch.setattr(
        settings_module.property_loader, "get_environment", lambda: "test"
    )
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_int_property_with_default",
        lambda key, default: 185,
    )

    settings = AgentEngineSettings.load()

    assert settings.engine == "n8n"
    assert settings.langgraph_api_key is None
    assert settings.langgraph_model_provider is None
    assert settings.n8n_timeout_seconds == 185


def test_langgraph_settings_are_provider_neutral(monkeypatch) -> None:
    values = {
        "langgraph_api_key": "secret",
    }
    defaults = {
        "agent_engine": "langgraph",
        "langgraph_model_provider": "anthropic",
        "langgraph_model": "claude-test",
        "langgraph_temperature": "0.4",
    }
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_string_property",
        values.__getitem__,
    )
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_string_property_with_default",
        lambda key, default: defaults.get(key, default),
    )
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_int_property_with_default",
        lambda key, default: 30,
    )
    monkeypatch.setattr(
        settings_module.property_loader, "get_environment", lambda: "test"
    )

    settings = AgentEngineSettings.load()

    assert settings.langgraph_model_provider == "anthropic"
    assert settings.langgraph_model == "claude-test"
    assert settings.langgraph_api_key == "secret"


@pytest.mark.parametrize(
    ("timeout", "temperature", "error"),
    [
        (0, "0.7", "LANGGRAPH_TIMEOUT_SECONDS must be a positive integer"),
        (60, "invalid", "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"),
        (60, "2.1", "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"),
    ],
)
def test_runtime_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
    timeout: int,
    temperature: str,
    error: str,
) -> None:
    defaults = {
        "agent_engine": "langgraph",
        "langgraph_model_provider": "groq",
        "langgraph_model": "test-model",
        "langgraph_temperature": temperature,
    }
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_string_property",
        lambda key: "test-key",
    )
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_int_property_with_default",
        lambda key, default: timeout,
    )
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_string_property_with_default",
        lambda key, default: defaults.get(key, default),
    )
    monkeypatch.setattr(
        settings_module.property_loader, "get_environment", lambda: "test"
    )
    with pytest.raises(ValueError, match=error):
        AgentEngineSettings.load()
