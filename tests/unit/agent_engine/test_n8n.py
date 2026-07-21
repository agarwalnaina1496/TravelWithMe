"""n8n raw-output adapter and workflow contract tests."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from twm.services import (
    AgentAdapterError,
    AgentAdapterTimeoutError,
    AgentEngineSettings,
    AgentInvocation,
    N8NAgentAdapter,
)


def settings() -> AgentEngineSettings:
    return AgentEngineSettings(
        engine="n8n",
        environment="test",
        n8n_scout_webhook_url="http://agents.example/webhook/scout",
        n8n_meridian_webhook_url="http://agents.example/webhook/meridian",
    )


def test_n8n_adapter_forwards_prepared_invocation_and_returns_raw_output() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"raw_output": '{"message":"ok"}'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = N8NAgentAdapter(settings(), client)

    try:
        result = asyncio.run(
            adapter.invoke(
                "scout",
                AgentInvocation(
                    system_prompt="system with schema",
                    user_prompt="untrusted traveler data",
                ),
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert result.raw_output == '{"message":"ok"}'
    assert result.metadata == {}
    assert captured[0].url == "http://agents.example/webhook/scout"
    assert json.loads(captured[0].content) == {
        "system_prompt": "system with schema",
        "user_prompt": "untrusted traveler data",
    }
    assert "X-TWM-Webhook-Token" not in captured[0].headers


def test_n8n_adapter_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = N8NAgentAdapter(settings(), client)

    try:
        with pytest.raises(AgentAdapterTimeoutError):
            asyncio.run(
                adapter.invoke("scout", AgentInvocation("system", "user"))
            )
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"detail": "unavailable"}),
        httpx.Response(200, json={"output": "legacy-wrapper"}),
        httpx.Response(200, text="not-json"),
    ],
)
def test_n8n_adapter_rejects_upstream_and_private_contract_errors(
    response: httpx.Response,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = N8NAgentAdapter(settings(), client)

    try:
        with pytest.raises(AgentAdapterError):
            asyncio.run(
                adapter.invoke("meridian", AgentInvocation("system", "user"))
            )
    finally:
        asyncio.run(client.aclose())


def _assert_workflow_is_thin_raw_adapter(
    workflow_name: str, agent_name: str
) -> None:
    workflow_path = Path(__file__).parents[3] / "n8n" / workflow_name
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert {"Webhook", agent_name, "Groq Chat Model", "Respond to Webhook"} == set(
        nodes
    )
    main_nodes = [
        node
        for node in workflow["nodes"]
        if node["type"] != "@n8n/n8n-nodes-langchain.lmChatGroq"
    ]
    assert len(main_nodes) == 3
    assert not any("outputParser" in node["type"] for node in workflow["nodes"])
    assert "hasOutputParser" not in nodes[agent_name]["parameters"]
    assert (
        nodes[agent_name]["parameters"]["text"]
        == "={{ $('Webhook').item.json.body.user_prompt }}"
    )
    assert (
        nodes[agent_name]["parameters"]["options"]["systemMessage"]
        == "={{ $('Webhook').item.json.body.system_prompt }}"
    )
    assert (
        nodes["Respond to Webhook"]["parameters"]["responseBody"]
        == "={{ { raw_output: $json.output } }}"
    )
    assert "authentication" not in nodes["Webhook"]["parameters"]
    assert "credentials" not in nodes["Webhook"]
    assert workflow["connections"][agent_name]["main"][0][0]["node"] == (
        "Respond to Webhook"
    )
    assert not any(
        "ai_outputParser" in connection
        for connection in workflow["connections"].values()
    )


def test_scout_workflow_is_thin_raw_adapter() -> None:
    _assert_workflow_is_thin_raw_adapter("scout.json", "Scout")


def test_meridian_workflow_is_thin_raw_adapter() -> None:
    _assert_workflow_is_thin_raw_adapter("meridian.json", "Meridian")
