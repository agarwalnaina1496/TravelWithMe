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


def invocation() -> AgentInvocation:
    return AgentInvocation("system", "user", {"type": "object"})


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
                    system_prompt="system",
                    user_prompt="untrusted traveler data",
                    output_schema={"type": "object"},
                ),
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert result.raw_output == '{"message":"ok"}'
    assert result.metadata == {}
    assert captured[0].url == "http://agents.example/webhook/scout"
    assert json.loads(captured[0].content) == {
        "system_prompt": "system",
        "user_prompt": "untrusted traveler data",
        "output_schema": {"type": "object"},
    }
    assert "X-TWM-Webhook-Token" not in captured[0].headers


def test_n8n_adapter_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = N8NAgentAdapter(settings(), client)

    try:
        with pytest.raises(AgentAdapterTimeoutError) as captured:
            asyncio.run(
                adapter.invoke("scout", invocation())
            )
    finally:
        asyncio.run(client.aclose())

    error = captured.value
    assert error.component == "n8n"
    assert error.failure_stage == "invocation"
    assert error.error_type == "ReadTimeout"
    assert error.detail == "slow"


def test_n8n_adapter_returns_empty_model_content_for_common_repair() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"raw_output": ""})
        )
    )
    adapter = N8NAgentAdapter(settings(), client)

    try:
        result = asyncio.run(adapter.invoke("scout", invocation()))
    finally:
        asyncio.run(client.aclose())

    assert result.raw_output == ""


def test_n8n_adapter_classifies_connection_failure_without_exposing_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("all connection attempts failed", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = N8NAgentAdapter(settings(), client)

    try:
        with pytest.raises(AgentAdapterError) as captured:
            asyncio.run(
                adapter.invoke("scout", invocation())
            )
    finally:
        asyncio.run(client.aclose())

    error = captured.value
    assert error.component == "n8n"
    assert error.failure_stage == "upstream_connection"
    assert error.error_type == "ConnectError"
    assert error.detail == "all connection attempts failed"


@pytest.mark.parametrize(
    ("response", "failure_stage", "error_type", "detail", "status_code"),
    [
        (
            httpx.Response(503, json={"detail": "unavailable"}),
            "upstream_http",
            "HTTPStatusError",
            "n8n returned HTTP 503",
            503,
        ),
        (
            httpx.Response(200, json={"output": "legacy-wrapper"}),
            "response_contract",
            "N8NResponseContractError",
            "n8n response did not contain a string raw_output",
            None,
        ),
        (
            httpx.Response(200, text="not-json"),
            "response_decode",
            "JSONDecodeError",
            "n8n returned a response that was not valid JSON",
            None,
        ),
    ],
)
def test_n8n_adapter_rejects_upstream_and_private_contract_errors(
    response: httpx.Response,
    failure_stage: str,
    error_type: str,
    detail: str,
    status_code: int | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = N8NAgentAdapter(settings(), client)

    try:
        with pytest.raises(AgentAdapterError) as captured:
            asyncio.run(
                adapter.invoke("meridian", invocation())
            )
    finally:
        asyncio.run(client.aclose())

    error = captured.value
    assert error.component == "n8n"
    assert error.failure_stage == failure_stage
    assert error.error_type == error_type
    assert error.detail == detail
    assert error.upstream_status_code == status_code


def _assert_workflow_uses_schema_constrained_generation(
    workflow_name: str, agent_name: str
) -> None:
    workflow_path = Path(__file__).parents[3] / "n8n" / workflow_name
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}

    parser_name = f"{agent_name} output schema"
    assert {
        "Webhook",
        agent_name,
        parser_name,
        "Groq Chat Model",
        "Respond to Webhook",
    } == set(nodes)
    main_nodes = [
        node
        for node in workflow["nodes"]
        if node["type"]
        not in {
            "@n8n/n8n-nodes-langchain.lmChatGroq",
            "@n8n/n8n-nodes-langchain.outputParserStructured",
        }
    ]
    assert len(main_nodes) == 3
    assert nodes[agent_name]["parameters"]["hasOutputParser"] is True
    assert nodes[parser_name]["parameters"] == {
        "schemaType": "manual",
        "inputSchema": (
            "={{ JSON.stringify($('Webhook').item.json.body.output_schema) }}"
        ),
    }
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
        == "={{ { raw_output: JSON.stringify($json.output) } }}"
    )
    assert "authentication" not in nodes["Webhook"]["parameters"]
    assert "credentials" not in nodes["Webhook"]
    assert workflow["connections"][agent_name]["main"][0][0]["node"] == (
        "Respond to Webhook"
    )
    assert workflow["connections"][parser_name]["ai_outputParser"][0][0] == {
        "node": agent_name,
        "type": "ai_outputParser",
        "index": 0,
    }
    assert workflow["settings"]["executionTimeout"] == 180


def test_scout_workflow_is_thin_raw_adapter() -> None:
    _assert_workflow_uses_schema_constrained_generation("scout.json", "Scout")


def test_meridian_workflow_is_thin_raw_adapter() -> None:
    _assert_workflow_uses_schema_constrained_generation(
        "meridian.json", "Meridian"
    )
