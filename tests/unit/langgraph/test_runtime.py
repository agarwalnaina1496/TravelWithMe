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
        generation_max_output_tokens=12_000,
        generation_temperature=0.3,
        generation_timeout_seconds=45,
    )

    runtime = LangGraphRuntime(settings=settings)

    assert runtime.model is model
    initializer.assert_called_once_with(
        model="openai/gpt-oss-120b",
        model_provider="groq",
        api_key="secret",
        temperature=0.3,
        max_tokens=12_000,
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
        lambda key, default: {
            "generation_max_output_tokens": 16_384,
            "generation_timeout_seconds": 180,
            "n8n_timeout_seconds": 185,
        }.get(key, default),
    )

    settings = AgentEngineSettings.load()

    assert settings.engine == "n8n"
    assert settings.langgraph_api_key is None
    assert settings.langgraph_model_provider is None
    assert settings.n8n_timeout_seconds == 185
    assert settings.generation_config.max_output_tokens == 16_384
    assert settings.generation_config.temperature == 0.2
    assert settings.generation_config.timeout_seconds == 180


def test_langgraph_settings_are_provider_neutral(monkeypatch) -> None:
    values = {
        "langgraph_api_key": "secret",
    }
    defaults = {
        "agent_engine": "langgraph",
        "langgraph_model_provider": "anthropic",
        "langgraph_model": "claude-test",
        "generation_temperature": "0.4",
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


def test_n8n_settings_reject_timeout_that_differs_from_workflow(
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
        settings_module.property_loader,
        "get_int_property_with_default",
        lambda key, default: {
            "generation_max_output_tokens": 16_384,
            "generation_timeout_seconds": 120,
            "n8n_timeout_seconds": 185,
        }.get(key, default),
    )
    monkeypatch.setattr(
        settings_module.property_loader, "get_environment", lambda: "test"
    )

    with pytest.raises(
        ValueError,
        match="GENERATION_TIMEOUT_SECONDS must be 180 for n8n",
    ):
        AgentEngineSettings.load()


@pytest.mark.parametrize(
    ("max_tokens", "timeout", "temperature", "error"),
    [
        (
            0,
            60,
            "0.2",
            "GENERATION_MAX_OUTPUT_TOKENS must be a positive integer",
        ),
        (
            16_384,
            0,
            "0.2",
            "GENERATION_TIMEOUT_SECONDS must be a positive integer",
        ),
        (
            16_384,
            60,
            "invalid",
            "GENERATION_TEMPERATURE must be a number between 0 and 2",
        ),
        (
            16_384,
            60,
            "2.1",
            "GENERATION_TEMPERATURE must be a number between 0 and 2",
        ),
    ],
)
def test_runtime_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
    max_tokens: int,
    timeout: int,
    temperature: str,
    error: str,
) -> None:
    defaults = {
        "agent_engine": "langgraph",
        "langgraph_model_provider": "groq",
        "langgraph_model": "test-model",
        "generation_temperature": temperature,
    }
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_string_property",
        lambda key: "test-key",
    )
    monkeypatch.setattr(
        settings_module.property_loader,
        "get_int_property_with_default",
        lambda key, default: {
            "generation_max_output_tokens": max_tokens,
            "generation_timeout_seconds": timeout,
        }.get(key, default),
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
