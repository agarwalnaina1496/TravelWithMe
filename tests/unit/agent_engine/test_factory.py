"""Configuration-driven engine selection tests."""

import pytest

from twm.services import N8NAgentEngine
from twm.services.agent_engine import factory
from twm.shared.properties import property_loader


def test_factory_selects_exact_configured_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory.property_loader,
        "get_string_property_with_default",
        lambda key, default: "n8n",
    )
    assert isinstance(factory.get_agent_engine(), N8NAgentEngine)

    sentinel = object()
    monkeypatch.setattr(
        factory.property_loader,
        "get_string_property_with_default",
        lambda key, default: "langgraph",
    )
    monkeypatch.setattr(factory, "LangGraphAgentEngine", lambda: sentinel)
    assert factory.get_agent_engine() is sentinel


def test_unknown_engine_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory.property_loader,
        "get_string_property_with_default",
        lambda key, default: "shadow",
    )
    with pytest.raises(ValueError, match="Unsupported AGENT_ENGINE: shadow"):
        factory.get_agent_engine()


def test_committed_default_is_n8n() -> None:
    assert property_loader.CONFIG.get("APP", "agent_engine") == "n8n"
