"""Common agent execution, validation, and repair tests."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from twm.prompts import PromptRelease
from twm.schemas import MeridianAgentOutput, ScoutAgentOutput
from twm.security import UNTRUSTED_DATA_PREAMBLE
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings, CorrelationContext, reset_correlation_context, set_correlation_context
from twm.services import (
    AgentAdapterTimeoutError,
    AgentExecutionService,
    AgentInvocationResult,
    AgentOutputError,
)
from twm.services.agent_engine import service as service_module
from tests.factories import recommendation_option, traveler_criteria


def service_with_outputs(
    monkeypatch, *outputs: str | AgentInvocationResult
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
    adapter.engine_name = "mock"
    adapter.endpoint = lambda agent: None
    monkeypatch.setattr(
        service_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test-version", f"{agent} prompt"),
    )
    return AgentExecutionService(adapter), adapter


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


def test_common_service_logs_attempt_metadata_without_content(
    monkeypatch, caplog
) -> None:
    output = {
        "message": "Private generated guidance.",
        "state_delta": {},
        "intent": "advise",
    }
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
    )
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    asyncio.run(engine.scout({}, "private traveler message"))

    assert "agent=scout attempt=1" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "raw_output_chars=" in caplog.text
    assert "finish_reason=stop" in caplog.text
    assert "input_tokens=120" in caplog.text
    assert "provider_attempts=1" in caplog.text
    assert "private traveler message" not in caplog.text
    assert "Private generated guidance" not in caplog.text


def test_common_service_validates_meridian_semantics(monkeypatch) -> None:
    output = meridian_success()
    engine, adapter = service_with_outputs(monkeypatch, json.dumps(output))

    execution = asyncio.run(engine.meridian({}, "Find options."))

    assert execution.response == MeridianAgentOutput.model_validate(
        output
    ).model_dump(mode="json", exclude_none=True)
    assert adapter.invoke.await_count == 1
    _, invocation = adapter.invoke.await_args.args
    assert '"destination_id"' in invocation.system_prompt
    assert '"circuit_id"' in invocation.system_prompt


def test_common_service_repairs_invalid_output_once(monkeypatch) -> None:
    repaired = {
        "message": "A repaired answer.",
        "state_delta": {},
        "intent": "advise",
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        "not-json",
        json.dumps(repaired),
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
    monkeypatch, caplog
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
    assert "output remained invalid" in caplog.text
    assert sensitive_key not in caplog.text


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


def test_agent_execution_emits_lifecycle_events_with_full_payload(monkeypatch) -> None:
    sink = InMemorySink()
    telemetry_logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 1024), sink
    )
    token = set_correlation_context(CorrelationContext("request-123", "trip-1", "turn-1"))
    try:
        engine, _ = service_with_outputs(monkeypatch, json.dumps({
            "message": "A mountain trip can work well.",
            "state_delta": {"trip_context": {"region": "Uttarakhand"}},
            "intent": "advise",
        }))
        engine = AgentExecutionService(engine._adapter, telemetry_logger=telemetry_logger)

        asyncio.run(engine.scout({"stage": "new", "trip_context": {}}, "Tell me about mountain trips."))

        events = sink.events
        assert any(event["event"] == "be.agent.input.prepared" for event in events)
        assert any(event["event"] == "be.agent.invocation.started" for event in events)
        assert any(event["event"] == "be.agent.raw_response.received" for event in events)
        assert any(event["event"] == "be.agent.output.validated" for event in events)
    finally:
        reset_correlation_context(token)


def test_agent_execution_emits_repair_events_on_validation_failure(monkeypatch) -> None:
    """Verify repair flow emits be.agent.output.validation_failed and be.agent.repair.started events."""
    sink = InMemorySink()
    telemetry_logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 1024), sink
    )
    repaired = {
        "message": "A repaired answer.",
        "state_delta": {},
        "intent": "advise",
    }
    engine, _ = service_with_outputs(
        monkeypatch,
        "not-json",  # Attempt 1: invalid JSON
        json.dumps(repaired),  # Attempt 2: valid JSON
    )
    engine = AgentExecutionService(engine._adapter, telemetry_logger=telemetry_logger)

    asyncio.run(engine.scout({}, "Help me."))

    events = sink.events
    # Attempt 1: input prepared and invocation started
    assert any(
        event["event"] == "be.agent.input.prepared"
        and event["fields"]["attempt"] == 1
        for event in events
    )
    assert any(
        event["event"] == "be.agent.invocation.started"
        and event["fields"]["attempt"] == 1
        for event in events
    )
    assert any(
        event["event"] == "be.agent.raw_response.received"
        and event["fields"]["attempt"] == 1
        for event in events
    )
    # Validation failure on attempt 1
    assert any(
        event["event"] == "be.agent.output.validation_failed"
        and event["fields"]["attempt"] == 1
        for event in events
    )
    # Repair started with attempt 2
    assert any(
        event["event"] == "be.agent.repair.started"
        and event["fields"]["attempt"] == 2
        for event in events
    )
    # Attempt 2: input prepared, invocation, and success
    assert any(
        event["event"] == "be.agent.input.prepared"
        and event["fields"]["attempt"] == 2
        for event in events
    )
    assert any(
        event["event"] == "be.agent.invocation.started"
        and event["fields"]["attempt"] == 2
        for event in events
    )
    assert any(
        event["event"] == "be.agent.raw_response.received"
        and event["fields"]["attempt"] == 2
        for event in events
    )
    assert any(
        event["event"] == "be.agent.output.validated"
        and event["fields"]["attempt"] == 2
        for event in events
    )


def test_agent_execution_emits_final_validation_failure_on_both_attempts(monkeypatch) -> None:
    """Verify final validation failure events when both attempt 1 and 2 fail."""
    sink = InMemorySink()
    telemetry_logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 1024), sink
    )
    invalid = {
        "message": "Invalid",
        "state_delta": {},
        "intent": "advise",
    }
    engine, _ = service_with_outputs(
        monkeypatch,
        "not-json",  # Attempt 1: invalid JSON
        "still-not-json",  # Attempt 2: also invalid JSON
    )
    engine = AgentExecutionService(engine._adapter, telemetry_logger=telemetry_logger)

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.scout({}, "Help me."))

    events = sink.events
    # Attempt 1 validation failure
    attempt_1_failures = [
        event for event in events
        if event["event"] == "be.agent.output.validation_failed"
        and event["fields"]["attempt"] == 1
    ]
    assert len(attempt_1_failures) == 1
    assert "failures" in attempt_1_failures[0]["payload"]
    # Repair started
    assert any(
        event["event"] == "be.agent.repair.started" for event in events
    )
    # Attempt 2 validation failure
    attempt_2_failures = [
        event for event in events
        if event["event"] == "be.agent.output.validation_failed"
        and event["fields"]["attempt"] == 2
    ]
    assert len(attempt_2_failures) == 1
    assert "failures" in attempt_2_failures[0]["payload"]
    # No be.agent.output.validated event (both failed)
    assert not any(
        event["event"] == "be.agent.output.validated" for event in events
    )


def test_agent_execution_emits_invocation_failed_on_adapter_timeout(monkeypatch) -> None:
    """Verify be.agent.invocation.failed event is emitted on adapter timeout."""
    sink = InMemorySink()
    telemetry_logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 1024), sink
    )
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=AgentAdapterTimeoutError("provider timed out")
    )
    adapter.engine_name = "mock"
    adapter.endpoint = lambda agent: None
    monkeypatch.setattr(
        service_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test-version", f"{agent} prompt"),
    )
    engine = AgentExecutionService(adapter, telemetry_logger=telemetry_logger)

    with pytest.raises(AgentAdapterTimeoutError):
        asyncio.run(engine.scout({}, "Help me."))

    events = sink.events
    # Invocation started
    assert any(
        event["event"] == "be.agent.invocation.started"
        and event["fields"]["attempt"] == 1
        for event in events
    )
    # Invocation failed with timeout error type
    failed_events = [
        event for event in events
        if event["event"] == "be.agent.invocation.failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["fields"]["attempt"] == 1
    assert failed_events[0]["fields"]["status"] == "failed"
    assert failed_events[0]["fields"]["error_type"] == "AgentAdapterTimeoutError"
    assert "duration_ms" in failed_events[0]["fields"]


def test_agent_execution_emits_invocation_failed_on_adapter_error(monkeypatch) -> None:
    """Verify be.agent.invocation.failed event is emitted on generic adapter error."""
    from twm.services import AgentAdapterError
    
    sink = InMemorySink()
    telemetry_logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.FULL, 1024), sink
    )
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(
        side_effect=AgentAdapterError("adapter connection failed")
    )
    adapter.engine_name = "mock"
    adapter.endpoint = lambda agent: None
    monkeypatch.setattr(
        service_module,
        "load_prompt_release",
        lambda agent: PromptRelease(agent, "test-version", f"{agent} prompt"),
    )
    engine = AgentExecutionService(adapter, telemetry_logger=telemetry_logger)

    with pytest.raises(AgentAdapterError):
        asyncio.run(engine.scout({}, "Help me."))

    events = sink.events
    # Invocation failed with generic adapter error type
    failed_events = [
        event for event in events
        if event["event"] == "be.agent.invocation.failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["fields"]["error_type"] == "AgentAdapterError"
