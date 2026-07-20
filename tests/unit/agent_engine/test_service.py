"""Common agent execution, validation, and repair tests."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from twm.prompts import PromptRelease
from twm.schemas import MeridianAgentOutput, ScoutAgentOutput
from twm.security import UNTRUSTED_DATA_PREAMBLE
from twm.services import AgentExecutionService, AgentOutputError
from twm.services.agent_engine import service as service_module
from tests.factories import recommendation_option, traveler_criteria


def service_with_outputs(monkeypatch, *outputs: str):
    adapter = AsyncMock()
    adapter.invoke = AsyncMock(side_effect=list(outputs))
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


def test_common_service_validates_meridian_semantics(monkeypatch) -> None:
    output = meridian_success()
    engine, adapter = service_with_outputs(monkeypatch, json.dumps(output))

    execution = asyncio.run(engine.meridian({}, "Find options."))

    assert execution.response == MeridianAgentOutput.model_validate(output).model_dump(
        mode="json", exclude_none=True
    )
    assert adapter.invoke.await_count == 1


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
    assert "UNTRUSTED_FAILED_COMPLETION" in repair_invocation.user_prompt
    repair_data = json.loads(repair_invocation.user_prompt.split("\n", 1)[1])
    original_invocation = adapter.invoke.await_args_list[0].args[1]
    assert repair_data["original_user_prompt"] == original_invocation.user_prompt
    assert repair_data["failed_completion"] == "not-json"
    assert repair_data["validation_failures"] == [
        {"type": "json_invalid", "loc": []}
    ]


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
    repair_data = json.loads(repair_invocation.user_prompt.split("\n", 1)[1])
    assert repair_data["validation_failures"] == [
        {"type": "extra_forbidden", "loc": ["<redacted>"]}
    ]
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
