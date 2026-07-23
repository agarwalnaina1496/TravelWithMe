"""Common agent execution, validation, and repair tests."""

import asyncio
import json
from pathlib import Path
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


REGRESSION_FIXTURES = Path(__file__).parents[2] / "fixtures" / "agent_engine"


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
    assert invocation.system_prompt == "scout prompt"
    assert invocation.output_schema == ScoutAgentOutput.model_json_schema()
    assert "intent" in invocation.output_schema["properties"]
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

    calling, validated = sink.events
    assert calling["message"] == (
        'Scout agent called via test-engine with message "private traveler message"'
    )
    assert calling["fields"] == {
        "agent": "scout",
        "engine": "test-engine",
        "attempt": 1,
        "prompt_version": "test-version",
    }
    assert set(calling["payload"]) == {"user_prompt"}
    assert "private traveler message" in calling["payload"]["user_prompt"]
    assert validated["message"].startswith(
        "Scout agent response received from test-engine. Response - "
    )
    assert "Private generated guidance" in validated["message"]
    assert "Private generated guidance" in validated["response"]["message"]
    assert validated["fields"]["finish_reason"] == "stop"
    assert validated["fields"]["input_tokens"] == 120
    assert validated["fields"]["provider_attempts"] == 1
    assert validated["fields"]["raw_output_chars"] == len(
        json.dumps(output)
    )


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
    assert invocation.output_schema == MeridianAgentOutput.model_json_schema()
    assert "options" in invocation.output_schema["properties"]
    assert [event["event"] for event in sink.events] == [
        "be.agent.invocation.started",
        "be.agent.response.received",
    ]
    assert sink.events[1]["message"].startswith(
        "Meridian agent response received from test-engine. Response - "
    )


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
    assert calling["message"] == (
        'Scout agent called via test-engine with message "Help me."'
    )
    assert failed["message"] == (
        f"Scout invocation via test-engine failed. Detail - "
        f"{error_type}: {error}"
    )
    assert failed["level"] == "ERROR"
    assert failed["fields"]["error_type"] == error_type
    assert failed["fields"]["component"] == "test-engine"
    assert failed["fields"]["operation"] == "scout.invoke"
    assert failed["fields"]["failure_stage"] == "invocation"
    assert failed["fields"]["error_detail"] == str(error)
    assert failed["fields"]["status"] == "failed"


def test_common_service_bounds_primary_message_but_preserves_diagnostic_detail(
    monkeypatch,
) -> None:
    sink = InMemorySink()
    engine, _ = service_with_outputs(monkeypatch, telemetry_sink=sink)
    long_detail = "provider failure " + ("x" * 1_000)
    engine._adapter.invoke.side_effect = RuntimeError(long_detail)

    with pytest.raises(RuntimeError):
        asyncio.run(engine.scout({}, "y" * 2_000))

    calling, failed = sink.events
    assert "...[TRUNCATED]" in calling["message"]
    assert "...[TRUNCATED]" in failed["message"]
    assert failed["fields"]["error_detail"] == long_detail


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
    assert repair_invocation.output_schema == original_invocation.output_schema
    assert "not-json" not in repair_invocation.system_prompt
    assert '"type":"json_invalid"' in repair_invocation.system_prompt
    assert [event["event"] for event in sink.events] == [
        "be.agent.invocation.started",
        "be.agent.output.validation_failed",
        "be.agent.repair.started",
        "be.agent.invocation.started",
        "be.agent.response.received",
    ]
    assert sink.events[-1]["message"].startswith(
        "Scout agent response received from test-engine. Response - "
    )
    assert sink.events[-1]["fields"]["attempt"] == 2
    assert sink.events[1]["fields"]["raw_output_chars"] == len("not-json")


def test_common_service_repairs_empty_successful_model_content(monkeypatch) -> None:
    repaired = {
        "message": "Recovered from empty content.",
        "state_delta": {},
        "intent": "advise",
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        "",
        json.dumps(repaired),
    )

    execution = asyncio.run(engine.scout({}, "Help me."))

    assert execution.response["message"] == "Recovered from empty content."
    assert adapter.invoke.await_count == 2


@pytest.mark.parametrize(
    "fixture_name",
    [
        "meridian_truncated_mid_option",
        "meridian_truncated_third_option",
        "meridian_success_with_null_options",
    ],
)
def test_captured_meridian_regressions_require_common_repair(
    monkeypatch, fixture_name: str
) -> None:
    fixtures = json.loads(
        (REGRESSION_FIXTURES / "captured_regressions.json").read_text(
            encoding="utf-8"
        )
    )
    captured_raw_output = fixtures[fixture_name]["raw_output"]
    engine, adapter = service_with_outputs(
        monkeypatch,
        captured_raw_output,
        json.dumps(meridian_success()),
    )

    execution = asyncio.run(engine.meridian({}, "Find options."))

    assert execution.response["status"] == "SUCCESS"
    assert adapter.invoke.await_count == 2
    repair = adapter.invoke.await_args_list[1].args[1]
    assert captured_raw_output not in repair.system_prompt
    assert captured_raw_output not in repair.user_prompt


def test_captured_empty_scout_success_requires_common_repair(monkeypatch) -> None:
    fixtures = json.loads(
        (REGRESSION_FIXTURES / "captured_regressions.json").read_text(
            encoding="utf-8"
        )
    )
    captured = fixtures["scout_empty_success"]
    repaired = {
        "message": "Recovered from captured empty content.",
        "state_delta": {},
        "intent": "advise",
    }
    engine, adapter = service_with_outputs(
        monkeypatch,
        captured["raw_output"],
        json.dumps(repaired),
    )

    execution = asyncio.run(engine.scout({}, "Help me."))

    assert captured["observed_raw_output_chars"] == 0
    assert execution.response["intent"] == "advise"
    assert adapter.invoke.await_count == 2


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
