"""LangGraph provider runtime configuration tests."""

from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from twm.services import LangGraphRuntime
from twm.services.langgraph import runtime as runtime_module


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


def test_runtime_requires_credentials_only_when_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(key: str) -> str:
        raise KeyError(key)

    monkeypatch.setattr(runtime_module.property_loader, "get_string_property", missing)
    with pytest.raises(ValueError, match="GROQ_API_KEY is required"):
        LangGraphRuntime()


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
    monkeypatch.setattr(
        runtime_module.property_loader, "get_string_property", lambda key: "test-key"
    )
    monkeypatch.setattr(
        runtime_module.property_loader,
        "get_int_property_with_default",
        lambda key, default: timeout,
    )
    monkeypatch.setattr(
        runtime_module.property_loader,
        "get_string_property_with_default",
        lambda key, default: temperature if key == "langgraph_temperature" else default,
    )
    with pytest.raises(ValueError, match=error):
        LangGraphRuntime()
