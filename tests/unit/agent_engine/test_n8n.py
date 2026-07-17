"""n8n engine fallback contract tests."""

import json
from pathlib import Path

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


def test_meridian_workflow_uses_backend_output_schema() -> None:
    workflow_path = Path(__file__).parents[3] / "n8n" / "meridian.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert nodes["Meridian"]["parameters"]["options"]["hasOutputParser"] is True
    assert (
        "body.output_schema"
        in nodes["Meridian output schema"]["parameters"]["inputSchema"]
    )
    assert workflow["connections"]["Meridian output schema"] == {
        "ai_outputParser": [
            [{"node": "Meridian", "type": "ai_outputParser", "index": 0}]
        ]
    }
    assert "typeof output === 'string'" in nodes["Output parser"]["parameters"][
        "jsCode"
    ]
