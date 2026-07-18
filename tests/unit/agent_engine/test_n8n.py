"""n8n engine fallback contract tests."""

import json
from pathlib import Path
from unittest.mock import Mock

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


def test_production_webhook_requires_https_and_server_auth(monkeypatch) -> None:
    values = {
        "n8n_scout_webhook_url": "https://agents.example/webhook/scout",
        "n8n_webhook_token": "server-secret",
    }
    monkeypatch.setattr(
        n8n_module.property_loader, "get_string_property", values.__getitem__
    )
    monkeypatch.setattr(
        n8n_module.property_loader, "get_environment", lambda: "prod"
    )
    response = Mock()
    response.json.return_value = {"output": {}}
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response
    monkeypatch.setattr(n8n_module.httpx, "Client", Mock(return_value=client))

    N8NAgentEngine()._forward(
        "scout", "n8n_scout_webhook_url", {"message": "travel"}
    )

    client.post.assert_called_once_with(
        "https://agents.example/webhook/scout",
        json={"message": "travel"},
        headers={"X-TWM-Webhook-Token": "server-secret"},
    )


def _assert_workflow_uses_backend_output_schema(
    workflow_name: str, agent_name: str
) -> None:
    workflow_path = Path(__file__).parents[3] / "n8n" / workflow_name
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    schema_node = f"{agent_name} output schema"

    assert nodes[agent_name]["parameters"]["hasOutputParser"] is True
    assert nodes["Webhook"]["parameters"]["authentication"] == "headerAuth"
    assert nodes["Webhook"]["credentials"]["httpHeaderAuth"]["name"] == "TWM webhook auth"
    assert "UNTRUSTED_TRAVELER_DATA" in nodes[agent_name]["parameters"]["text"]
    assert "hasOutputParser" not in nodes[agent_name]["parameters"]["options"]
    assert (
        "body.output_schema"
        in nodes[schema_node]["parameters"]["inputSchema"]
    )
    assert workflow["connections"][schema_node] == {
        "ai_outputParser": [
            [{"node": agent_name, "type": "ai_outputParser", "index": 0}]
        ]
    }
    assert "Output parser" not in nodes
    assert workflow["connections"][agent_name] == {
        "main": [
            [{"node": "Respond to Webhook", "type": "main", "index": 0}]
        ]
    }
    assert (
        nodes["Respond to Webhook"]["parameters"]["responseBody"]
        == "={{ $json.output }}"
    )


def test_meridian_workflow_uses_backend_output_schema() -> None:
    _assert_workflow_uses_backend_output_schema("meridian.json", "Meridian")


def test_scout_workflow_uses_backend_output_schema() -> None:
    _assert_workflow_uses_backend_output_schema("scout.json", "Scout")
