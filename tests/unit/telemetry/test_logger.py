from datetime import datetime, timezone
from io import StringIO
import json
from uuid import UUID

import pytest

from twm.telemetry import (
    CorrelationContext,
    InMemorySink,
    JsonStdoutSink,
    PayloadMode,
    TelemetryLogger,
    TelemetrySettings,
    reset_correlation_context,
    set_correlation_context,
)
from twm.telemetry.sanitization import REDACTED


def settings(
    *, enabled: bool = True, payload_mode: PayloadMode = PayloadMode.METADATA,
    max_field_size: int = 64,
) -> TelemetrySettings:
    return TelemetrySettings(
        enabled=enabled,
        environment="test",
        payload_mode=payload_mode,
        max_field_size=max_field_size,
    )


def test_logger_emits_one_versioned_json_line_with_request_context() -> None:
    stream = StringIO()
    logger = TelemetryLogger(
        settings(),
        JsonStdoutSink(stream),
        clock=lambda: datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc),
    )
    token = set_correlation_context(
        CorrelationContext("request-1", "trip-1", "turn-1")
    )
    try:
        logger.event("test.event", source="unit", fields={"ok": True})
    finally:
        reset_correlation_context(token)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event == {
        "schema_version": "1.0",
        "timestamp": "2026-07-21T08:00:00+00:00",
        "level": "INFO",
        "environment": "test",
        "service": "travelwithme-backend",
        "source": "unit",
        "event": "test.event",
        "message": "test.event",
        "request_id": "request-1",
        "trip_id": "trip-1",
        "turn_id": "turn-1",
        "fields": {"ok": True},
    }


def test_redaction_and_size_limits_apply_before_sink() -> None:
    sink = InMemorySink()
    logger = TelemetryLogger(settings(max_field_size=20), sink)

    logger.event(
        "test.sanitize",
        fields={
            "authorization": "Bearer private",
            "access_token": "private",
            "input_tokens": 120,
            "nested": {"api_key": "private", "safe": "x" * 100},
        },
    )

    fields = sink.events[0]["fields"]
    assert fields["authorization"] == REDACTED
    assert fields["access_token"] == REDACTED
    assert fields["input_tokens"] == 120
    assert fields["nested"]["api_key"] == REDACTED
    assert len(fields["nested"]["safe"]) == 20
    assert fields["nested"]["safe"].endswith("...[TRUNCATED]")


def test_serialized_diagnostic_strings_redact_credential_values() -> None:
    sink = InMemorySink()
    logger = TelemetryLogger(
        settings(payload_mode=PayloadMode.FULL, max_field_size=1024), sink
    )

    logger.info(
        "Calling Scout",
        event="be.agent.invocation.started",
        payload={
            "user_prompt": (
                'data={"api_key":"private-key","input_tokens":120,'
                '"webhook_url":"https://private.test/hook"}'
            )
        },
        response='{"authorization":"Bearer private-token"}',
    )

    event = sink.events[0]
    assert "private-key" not in event["payload"]["user_prompt"]
    assert "private.test" not in event["payload"]["user_prompt"]
    assert '"input_tokens":120' in event["payload"]["user_prompt"]
    assert "private-token" not in event["response"]
    assert REDACTED in event["payload"]["user_prompt"]


@pytest.mark.parametrize(
    ("mode", "present", "absent"),
    [
        (PayloadMode.OFF, None, ("payload", "payload_metadata")),
        (PayloadMode.METADATA, "payload_metadata", ("payload",)),
        (PayloadMode.FULL, "payload", ("payload_metadata",)),
    ],
)
def test_payload_mode_controls_content(mode, present, absent) -> None:
    sink = InMemorySink()
    TelemetryLogger(settings(payload_mode=mode), sink).event(
        "test.payload", payload={"message": "hello"}
    )

    event = sink.events[0]
    if present:
        assert present in event
    for key in absent:
        assert key not in event


@pytest.mark.parametrize(
    ("method", "level"),
    [
        ("debug", "DEBUG"),
        ("info", "INFO"),
        ("warning", "WARNING"),
        ("error", "ERROR"),
        ("critical", "CRITICAL"),
    ],
)
def test_logging_style_methods_emit_readable_message_and_fields(
    method, level
) -> None:
    sink = InMemorySink()
    logger = TelemetryLogger(settings(payload_mode=PayloadMode.FULL), sink)

    getattr(logger, method)(
        "Calling Scout",
        event="be.agent.invocation.started",
        agent="scout",
        attempt=1,
        payload={"message": "hello"},
        response={"message": "world"},
    )

    event = sink.events[0]
    assert event["message"] == "Calling Scout"
    assert event["level"] == level
    assert event["fields"] == {"agent": "scout", "attempt": 1}
    assert event["payload"] == {"message": "hello"}
    assert event["response"] == {"message": "world"}


def test_metadata_mode_describes_payload_and_response_without_content() -> None:
    sink = InMemorySink()

    TelemetryLogger(settings(), sink).info(
        "Scout response received",
        event="be.agent.raw_response.received",
        payload={"private": "input"},
        response={"private": "output"},
    )

    event = sink.events[0]
    assert "payload" not in event
    assert "response" not in event
    assert event["payload_metadata"]["type"] == "dict"
    assert event["response_metadata"]["type"] == "dict"


def test_disabled_logger_and_broken_sink_are_fail_open() -> None:
    class BrokenSink:
        def emit(self, event):
            raise RuntimeError("destination unavailable")

    disabled_sink = InMemorySink()
    TelemetryLogger(settings(enabled=False), disabled_sink).event("ignored")
    TelemetryLogger(settings(), BrokenSink()).event("also.ignored")

    assert disabled_sink.events == []


def test_event_without_request_context_still_has_valid_request_id() -> None:
    sink = InMemorySink()
    TelemetryLogger(settings(), sink).event("test.background")

    UUID(sink.events[0]["request_id"])
