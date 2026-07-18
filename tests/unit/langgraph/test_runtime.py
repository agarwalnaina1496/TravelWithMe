"""LangGraph provider runtime configuration tests."""

from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from twm.services import AgentEngineSettings, LangGraphRuntime
from twm.services.agent_engine import settings as settings_module


class StructuredOutput(BaseModel):
    message: str


def test_runtime_prepares_provider_structured_output() -> None:
    model = Mock()
    structured = object()
    model.with_structured_output.return_value = structured
    runtime = LangGraphRuntime(model=model)

    assert runtime.structured_model(StructuredOutput) is structured
    model.with_structured_output.assert_called_once_with(
        StructuredOutput,
        method="json_schema",
        include_raw=True,
        strict=False,
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

    settings = AgentEngineSettings.load()

    assert settings.engine == "n8n"
    assert settings.groq_api_key is None


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
    values = {"agent_engine": "langgraph", "langgraph_temperature": temperature}
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
        lambda key, default: values.get(key, default),
    )
    monkeypatch.setattr(
        settings_module.property_loader, "get_environment", lambda: "test"
    )
    with pytest.raises(ValueError, match=error):
        AgentEngineSettings.load()
