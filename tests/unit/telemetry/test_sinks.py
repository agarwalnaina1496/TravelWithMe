from io import StringIO

from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from twm.telemetry import CompositeSink, InMemorySink, JsonStdoutSink, OtlpHttpSink
from twm.telemetry.sinks import build_telemetry_sink


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events = []
        self.fail = fail
        self.shutdown_called = False

    def emit(self, event) -> None:
        if self.fail:
            raise RuntimeError("unavailable")
        self.events.append(dict(event))

    def shutdown(self) -> None:
        self.shutdown_called = True
        if self.fail:
            raise RuntimeError("unavailable")


def test_composite_sink_isolates_delivery_and_shutdown_failures() -> None:
    broken = RecordingSink(fail=True)
    healthy = RecordingSink()
    sink = CompositeSink(broken, healthy)

    sink.emit({"event": "test.event"})
    sink.shutdown()

    assert healthy.events == [{"event": "test.event"}]
    assert healthy.shutdown_called is True


def test_non_production_never_constructs_otlp_sink(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://example.test/v1/logs")
    stdout = InMemorySink()

    selected = build_telemetry_sink(
        environment="test",
        service="test-service",
        stdout_sink=stdout,
        otlp_factory=lambda _: (_ for _ in ()).throw(
            AssertionError("OTLP must remain disabled outside production")
        ),
    )

    assert selected is stdout


def test_production_composes_stdout_and_otlp_when_endpoint_exists(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://example.test/v1/logs")
    stdout = InMemorySink()
    otlp = RecordingSink()

    selected = build_telemetry_sink(
        environment="prod",
        service="test-service",
        stdout_sink=stdout,
        otlp_factory=lambda service: otlp,
    )
    selected.emit({"event": "test.event"})

    assert stdout.events == [{"event": "test.event"}]
    assert otlp.events == [{"event": "test.event"}]


def test_missing_endpoint_keeps_stdout_only_in_production(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    stdout = JsonStdoutSink(StringIO())

    selected = build_telemetry_sink(
        environment="prod",
        service="test-service",
        stdout_sink=stdout,
    )

    assert selected is stdout


def test_invalid_otlp_configuration_fails_open_to_stdout(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "invalid")
    stdout = InMemorySink()

    selected = build_telemetry_sink(
        environment="prod",
        service="test-service",
        stdout_sink=stdout,
        otlp_factory=lambda _: (_ for _ in ()).throw(
            ValueError("invalid exporter configuration")
        ),
    )

    assert selected is stdout


def test_otlp_sink_preserves_structured_body_and_queryable_attributes() -> None:
    provider = LoggerProvider()
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    otel_logger = provider.get_logger("test")
    sink = OtlpHttpSink(
        "test-service", otel_logger=otel_logger, provider=provider
    )
    event = {
        "timestamp": "2026-07-21T08:00:00+00:00",
        "level": "WARNING",
        "service": "test-service",
        "event": "be.http.response.sent",
        "message": "Sent Scout HTTP response",
        "request_id": "request-1",
        "fields": {
            "status_code": 502,
            "validation_failures": [
                {"type": "missing", "loc": ["options", 0]}
            ],
        },
    }

    sink.emit(event)
    provider.force_flush()

    exported = exporter.get_finished_logs()[0].log_record

    assert exported.body == "Sent Scout HTTP response"
    assert exported.severity_text == "WARNING"
    assert exported.attributes["request_id"] == "request-1"
    assert exported.attributes["message"] == "Sent Scout HTTP response"
    assert exported.attributes["fields.status_code"] == 502
    assert exported.attributes["fields.validation_failures.0.type"] == "missing"
    assert exported.attributes["fields.validation_failures.0.loc.0"] == "options"
    assert exported.attributes["fields.validation_failures.0.loc.1"] == 0

    sink.shutdown()
