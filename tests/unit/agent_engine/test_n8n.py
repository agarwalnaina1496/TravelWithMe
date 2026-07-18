"""n8n engine fallback contract tests."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from twm.prompts import PromptRelease
from twm.services import AgentEngineSettings, N8NAgentEngine
from twm.services.agent_engine import n8n as n8n_module


def test_n8n_transport_authenticates_and_canonicalizes_wrappers(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"json": {"output": {"message": "ok"}}}])

    settings = AgentEngineSettings(
        engine="n8n",
        environment="prod",
        n8n_scout_webhook_url="https://agents.example/webhook/scout",
        n8n_meridian_webhook_url="https://agents.example/webhook/meridian",
        n8n_webhook_token="server-secret",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = N8NAgentEngine(settings, client)
    monkeypatch.setattr(
        n8n_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test", "prompt"),
    )

    execution = asyncio.run(engine.scout({}, "travel"))
    asyncio.run(client.aclose())

    assert execution.response == {"message": "ok"}
    assert captured[0].headers["X-TWM-Webhook-Token"] == "server-secret"
    assert captured[0].url == "https://agents.example/webhook/scout"


def test_n8n_transport_propagates_upstream_errors(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    settings = AgentEngineSettings(
        engine="n8n",
        environment="test",
        n8n_scout_webhook_url="https://agents.test/scout",
        n8n_webhook_token="token",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = N8NAgentEngine(settings, client)
    monkeypatch.setattr(
        n8n_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test", "prompt"),
    )

    try:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(engine.scout({}, "travel"))
    finally:
        asyncio.run(client.aclose())


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
