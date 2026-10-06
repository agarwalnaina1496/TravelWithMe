"""Engine-level tolerance: retry policy, syntax slips, healed-response telemetry (TWM-234)."""

import asyncio
import json

import pytest

from tests.unit.agent_engine.test_service import service_with_outputs
from twm.services import AgentOutputError
from twm.services.agent_engine import OutputRetryPolicy
from twm.telemetry import InMemorySink

from tests.unit.agent_engine.tolerance_support import (
    scrambled_ranks,
    meridian_success,
    run,
    engine_with,
    SCOUT_REPLY,
)


def test_scout_unknown_top_level_key_is_dropped(monkeypatch):
    response, attempts, _ = run(
        monkeypatch, "scout", {"message": "Hello", "intent": "advise", "reasoning": "private chain of thought"}
    )

    assert attempts == 1
    assert "reasoning" not in response


# What stays rejected, and why. Each of these protects a consumer or the trust
# boundary, so a retry (and, failing that, an error) is the right outcome.


def test_scout_casing_and_null_state_delta_heal(monkeypatch):
    response, attempts, _ = run(
        monkeypatch, "scout", {"message": "Hello", "intent": "Advise", "state_delta": None}
    )

    assert attempts == 1
    assert response["intent"] == "advise"


def test_a_failing_response_gets_exactly_one_corrective_retry(monkeypatch):
    broken = json.dumps({"status": "SUCCESS", "message": "x", "state_delta": {}, "options": []})
    engine, adapter = engine_with(monkeypatch, [broken, broken])

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.meridian({}, "message"))

    assert adapter.invoke.await_count == 2


def test_a_new_attempt_is_not_started_when_it_would_overrun_the_time_budget(monkeypatch):
    broken = json.dumps({"status": "SUCCESS", "message": "x", "state_delta": {}, "options": []})
    # Any attempt takes longer than the budget allows another of, so one try only.
    engine, adapter = engine_with(
        monkeypatch, [broken, broken], retry=OutputRetryPolicy(budget_seconds=0.0)
    )

    with pytest.raises(AgentOutputError):
        asyncio.run(engine.meridian({}, "message"))

    assert adapter.invoke.await_count == 1


def test_a_healed_response_emits_a_queryable_event_with_provenance(monkeypatch):
    sink = InMemorySink()
    engine, _ = service_with_outputs(
        monkeypatch, json.dumps(scrambled_ranks(meridian_success())), telemetry_sink=sink
    )

    asyncio.run(engine.meridian({}, "message"))

    event = next(e for e in sink.events if e["event"] == "be.agent.output.normalized")
    assert event["fields"]["agent"] == "meridian"
    assert event["fields"]["prompt_version"] == "test-version"
    assert event["fields"]["normalizations_applied"] == ["options.ranked_by_position"]
    assert event["fields"]["normalization_count"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        '{"message": "Hello there", "intent": "advise",}',
        '{"message": "Hello there", "intent": "advise", "state_delta": None}',
        '{"message": "Hello there", // the reply\n "intent": "advise"}',
        '{"message": "Hello there", /* note */ "intent": "advise"}',
        '{"message": "Hello\nthere", "intent": "advise"}',
        'Sure! Here is the JSON:\n```json\n{"message": "Hello there", "intent": "advise",}\n```\nHope that helps.',
        '{"message": "Hello there", "intent": "advise", "state_delta": {"trip_context": {"flag": True, "gone": False,},},}',
    ],
    ids=["trailing-comma", "python-none", "line-comment", "block-comment", "raw-newline", "fenced-with-prose", "nested-python-literals"],
)
def test_a_syntax_slip_is_read_without_a_retry(monkeypatch, raw):
    engine, adapter = service_with_outputs(monkeypatch, raw)

    result = asyncio.run(engine.scout({}, "message"))

    assert adapter.invoke.await_count == 1
    assert result.response["message"].replace("\n", " ") == "Hello there"


def test_repairs_never_touch_text_inside_a_string(monkeypatch):
    tricky = '{"message": "None of these, True story, // not a comment, /* nor this */, trailing,]", "intent": "advise",}'
    engine, _ = service_with_outputs(monkeypatch, tricky)

    result = asyncio.run(engine.scout({}, "message"))

    assert result.response["message"] == "None of these, True story, // not a comment, /* nor this */, trailing,]"


@pytest.mark.parametrize(
    "raw",
    ['{"message": "cut off", "intent": "adv', '{"message": \'single quoted\'}'],
    ids=["truncated", "single-quoted"],
)
def test_output_that_changes_meaning_is_still_retried(monkeypatch, raw):
    engine, adapter = service_with_outputs(monkeypatch, raw, SCOUT_REPLY)

    result = asyncio.run(engine.scout({}, "message"))

    assert adapter.invoke.await_count == 2
    assert result.response["message"] == "Hello there"


def test_the_retry_names_a_syntax_failure_plainly(monkeypatch):
    engine, adapter = service_with_outputs(monkeypatch, '{"message": "cut off', SCOUT_REPLY)

    asyncio.run(engine.scout({}, "message"))

    assert "not one complete, valid JSON object" in adapter.invoke.await_args_list[1].args[1].system_prompt


def test_a_crashing_validator_is_a_failed_attempt_not_a_server_error(monkeypatch):
    from twm.schemas import ScoutAgentOutput

    def explode(*args, **kwargs):
        raise RuntimeError("a healer had a bug")

    monkeypatch.setattr(ScoutAgentOutput, "model_validate", explode)
    sink = InMemorySink()
    engine, adapter = service_with_outputs(monkeypatch, SCOUT_REPLY, SCOUT_REPLY, telemetry_sink=sink)

    with pytest.raises(AgentOutputError) as captured:
        asyncio.run(engine.scout({}, "message"))

    assert adapter.invoke.await_count == 2
    assert captured.value.failures[0]["type"] == "validator_error"
