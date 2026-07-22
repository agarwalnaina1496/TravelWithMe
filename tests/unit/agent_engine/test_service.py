"""Common agent execution, validation, and repair tests."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from twm.prompts import PromptRelease
from twm.schemas import MeridianAgentOutput, ScoutAgentOutput
from twm.security import UNTRUSTED_DATA_PREAMBLE
from twm.services import (
    AgentAdapterTimeoutError,
    AgentExecutionService,
    AgentInvocationResult,
    AgentOutputError,
)
from twm.services.agent_engine import service as service_module
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings
from tests.factories import recommendation_option, traveler_criteria


def service_with_outputs(
    monkeypatch,
    *outputs: str | AgentInvocationResult,
    telemetry_sink: InMemorySink | None = None,
):
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=[
            output
            if isinstance(output, AgentInvocationResult)
            else AgentInvocationResult(raw_output=output)
            for output in outputs
        ]
    )
    monkeypatch.setattr(
        service_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test-version", f"{agent} prompt"),
    )
    sink = telemetry_sink or InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 16_384), sink
    )
    return AgentExecutionService(adapter, logger, "test-engine"), adapter


def meridian_success() -> dict:
    return {
        "status": "SUCCESS",
        "message": "The first option is the strongest fit.",
        "state_delta": {
            "matcher_state": {"conversation_context": {"awaiting": None}}
        },
        "trip_type": "single",
        "traveler_criteria": traveler_criteria(),
        "options": [recommendation_option()],
    }


def test_common_service_prepares_and_validates_scout(monkeypatch) -> None:
    output = {
        "message": "A mountain trip can work well.",
        "state_delta": {"trip_context": {"region": "Uttarakhand"}},
        "intent": "advise",
    }
    engine, adapter = service_with_outputs(monkeypatch, json.dumps(output))

    execution = asyncio.run(
        engine.scout(
            {"stage": "new", "trip_context": {}},
            "Tell me about mountain trips.",
        )
    )

    assert execution.response == ScoutAgentOutput.model_validate(output).model_dump(
        mode="json", exclude_none=True
    )
    assert execution.prompt_release.version == "test-version"
    agent, invocation = adapter.invoke.await_args.args
    assert agent == "scout"
    assert invocation.system_prompt.startswith("scout prompt")
    assert "Return only one JSON object" in invocation.system_prompt
    assert '"intent"' in invocation.system_prompt
    assert invocation.user_prompt.startswith(UNTRUSTED_DATA_PREAMBLE)
    assert json.loads(
        invocation.user_prompt.removeprefix(UNTRUSTED_DATA_PREAMBLE)
    ) == {
        "trip_state": {"stage": "new", "trip_context": {}},
        "message": "Tell me about mountain trips.",
    }


def test_common_service_logs_engine_input_response_and_attempt_metadata(
    monkeypatch,
) -> None:
    output = {
        "message": "Private generated guidance.",
        "state_delta": {},
        "intent": "advise",
    }
    sink = InMemorySink()
    engine, _ = service_with_outputs(
        monkeypatch,
        AgentInvocationResult(
            raw_output=json.dumps(output),
            metadata={
                "finish_reason": "stop",
                "input_tokens": 120,
                "output_tokens": 40,
                "reasoning_tokens": 8,
                "total_tokens": 160,
                "queue_time_ms": 2.5,
                "model_time_ms": 40.0,
                "provider_total_time_ms": 42.5,
                "provider_attempts": 1,
            },
        ),
        telemetry_sink=sink,
    )

    asyncio.run(engine.scout({}, "private traveler message"))

    calling, received, validated = sink.events
    assert calling["message"] == "Calling Scout"
    assert calling["fields"] == {
        "agent": "scout",
        "engine": "test-engine",
        "attempt": 1,
        "prompt_version": "test-version",
    }
    assert "private traveler message" in calling["payload"]["user_prompt"]
    assert received["message"] == "Scout response received"
    assert "Private generated guidance" in received["response"]
    assert received["fields"]["finish_reason"] == "stop"
    assert received["fields"]["input_tokens"] == 120
    assert received["fields"]["provider_attempts"] == 1
    assert validated["message"] == "Scout response validated"


def test_common_service_validates_meridian_semantics(monkeypatch) -> None:
    output = meridian_success()
    sink = InMemorySink()
    engine, adapter = service_with_outputs(
        monkeypatch, json.dumps(output), telemetry_sink=sink
    )

    execution = asyncio.run(engine.meridian({}, "Find options."))

    assert execution.response == MeridianAgentOutput.model_validate(
        output
    ).model_dump(mode="json", exclude_none=True)
    assert adapter.invoke.await_count == 1
    _, invocation = adapter.invoke.await_args.args
    assert '"destination_id"' in invocation.system_prompt
    assert '"circuit_id"' in invocation.system_prompt
    assert [event["message"] for event in sink.events] == [
        "Calling Meridian",
        "Meridian response received",
        "Meridian response validated",
    ]


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (AgentAdapterTimeoutError("timed out"), "AgentAdapterTimeoutError"),
        (RuntimeError("adapter unavailable"), "RuntimeError"),
    ],
)
def test_common_service_logs_distinguishable_invocation_failures(
    monkeypatch, error, error_type
) -> None:
    sink = InMemorySink()
    engine, _ = service_with_outputs(
        monkeypatch,
        telemetry_sink=sink,
    )
    engine._adapter.invoke.side_effect = error

    with pytest.raises(type(error)):
        asyncio.run(engine.scout({}, "Help me."))

    calling, failed = sink.events
    assert calling["message"] == "Calling Scout"
    assert failed["message"] == "Scout invocation failed"
    assert failed["level"] == "ERROR"
    assert failed["fields"]["error_type"] == error_type
    assert failed["fields"]["status"] == "failed"


def test_common_service_repairs_invalid_output_once(monkeypatch) -> None:
    sink = InMemorySink()
    repaired = {
        "message": "A repaired answer.",
        "state_delta": {},
        "intent": "advise",
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        "not-json",
        json.dumps(repaired),
        telemetry_sink=sink,
    )

    execution = asyncio.run(engine.scout({}, "Help me."))

    assert execution.response == ScoutAgentOutput.model_validate(repaired).model_dump(
        mode="json", exclude_none=True
    )
    assert adapter.invoke.await_count == 2
    _, repair_invocation = adapter.invoke.await_args.args
    assert "previous completion failed" in repair_invocation.system_prompt
    original_invocation = adapter.invoke.await_args_list[0].args[1]
    assert repair_invocation.user_prompt == original_invocation.user_prompt
    assert "not-json" not in repair_invocation.system_prompt
    assert '"type":"json_invalid"' in repair_invocation.system_prompt
    assert [event["message"] for event in sink.events] == [
        "Calling Scout",
        "Scout response received",
        "Scout response failed validation",
        "Starting Scout repair attempt",
        "Calling Scout",
        "Scout response received",
        "Scout response validated",
    ]
    assert sink.events[4]["fields"]["attempt"] == 2


def test_common_service_raises_after_exactly_one_failed_repair(monkeypatch) -> None:
    invalid = {
        "status": "HARD_FAIL",
        "message": "Invalid because conversation context is missing.",
        "state_delta": {},
        "options": [],
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        json.dumps(invalid),
        json.dumps(invalid),
    )

    with pytest.raises(AgentOutputError) as captured:
        asyncio.run(engine.meridian({}, "Find options."))

    assert adapter.invoke.await_count == 2
    assert captured.value.agent == "meridian"
    assert captured.value.failures


def test_common_service_redacts_model_controlled_validation_locations(
    monkeypatch,
) -> None:
    sensitive_key = "passport_ABC123"
    invalid = {
        "message": "Invalid",
        "state_delta": {},
        "intent": "advise",
        sensitive_key: "secret",
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        json.dumps(invalid),
        json.dumps(invalid),
    )

    with pytest.raises(AgentOutputError) as captured:
        asyncio.run(engine.scout({}, "Help me."))

    repair_invocation = adapter.invoke.await_args_list[1].args[1]
    assert '"type":"extra_forbidden"' in repair_invocation.system_prompt
    assert '"loc":["<redacted>"]' in repair_invocation.system_prompt
    assert captured.value.failures == [
        {"type": "extra_forbidden", "loc": ["<redacted>"]}
    ]


def test_common_service_repairs_ui_owned_state_instead_of_applying_it(
    monkeypatch,
) -> None:
    invalid = {
        "message": "Invalid",
        "state_delta": {
            "trip_context": {"selected_option": {"id": "ui-owned"}}
        },
        "intent": "advise",
    }
    repaired = {
        "message": "Valid",
        "state_delta": {},
        "intent": "advise",
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        json.dumps(invalid),
        json.dumps(repaired),
    )

    execution = asyncio.run(engine.scout({}, "Select it."))

    assert execution.response == ScoutAgentOutput.model_validate(repaired).model_dump(
        mode="json", exclude_none=True
    )
    assert adapter.invoke.await_count == 2


def test_large_truncated_completion_is_not_copied_into_regeneration(
    monkeypatch,
) -> None:
    truncated = '{"message":"' + ("x" * 12_000)
    engine, adapter = service_with_outputs(
        monkeypatch, truncated, json.dumps(meridian_success())
    )

    execution = asyncio.run(engine.meridian({}, "Find options."))

    assert execution.response["options"][0]["destination_id"]
    retry = adapter.invoke.await_args_list[1].args[1]
    assert truncated not in retry.system_prompt
    assert truncated not in retry.user_prompt
