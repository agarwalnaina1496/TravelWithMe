"""n8n engine fallback contract tests."""

from twm.prompts import PromptRelease
from twm.services import N8NAgentEngine
from twm.services.agent_engine import n8n as n8n_module
from twm.services.response_normalization import (
    _normalize_meridian_response,
    _normalize_scout_response,
)


def test_configuration_failures_preserve_each_agent_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        n8n_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test", "prompt"),
    )
    monkeypatch.setattr(
        n8n_module.property_loader,
        "get_string_property",
        lambda key: (_ for _ in ()).throw(KeyError(key)),
    )
    engine = N8NAgentEngine()

    scout = _normalize_scout_response(engine.scout({}, "hello"))
    meridian = _normalize_meridian_response(engine.meridian({}, "find options"))

    assert scout.state_delta.model_dump() == {"trip_context": {}}
    assert meridian.status == "HARD_FAIL"
    assert meridian.state_delta.matcher_state == {
        "conversation_context": {"awaiting": None}
    }
