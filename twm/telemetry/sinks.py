"""Replaceable telemetry delivery boundaries."""

from collections.abc import Mapping, Sequence
from datetime import datetime
import json
import os
import sys
from time import time_ns
from typing import Any, Callable, Protocol, TextIO

from opentelemetry._logs import Logger as OtelLogger, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


OTLP_LOGS_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"
_OTLP_SEVERITY = {
    "DEBUG": SeverityNumber.DEBUG,
    "INFO": SeverityNumber.INFO,
    "WARNING": SeverityNumber.WARN,
    "ERROR": SeverityNumber.ERROR,
    "CRITICAL": SeverityNumber.FATAL,
}


class TelemetrySink(Protocol):
    def emit(self, event: Mapping[str, Any]) -> None: ...

    def shutdown(self) -> None: ...


class JsonStdoutSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def emit(self, event: Mapping[str, Any]) -> None:
        line = json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        self._stream.write(f"{line}\n")
        self._stream.flush()

    def shutdown(self) -> None:
        return None


class CompositeSink:
    """Fan out independently so one destination cannot suppress another."""

    def __init__(self, *sinks: TelemetrySink) -> None:
        self._sinks = sinks

    def emit(self, event: Mapping[str, Any]) -> None:
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:
                continue

    def shutdown(self) -> None:
        for sink in self._sinks:
            try:
                sink.shutdown()
            except Exception:
                continue


class OtlpHttpSink:
    """Export structured events with the standard OTLP/HTTP logs protocol."""

    def __init__(
        self,
        service: str,
        *,
        otel_logger: OtelLogger | None = None,
        provider: LoggerProvider | None = None,
    ) -> None:
        if otel_logger is None:
            provider = LoggerProvider(
                resource=Resource.create({"service.name": service})
            )
            provider.add_log_record_processor(
                BatchLogRecordProcessor(OTLPLogExporter())
            )
            otel_logger = provider.get_logger("twm.telemetry", "1.0")
        self._logger = otel_logger
        self._provider = provider

    def emit(self, event: Mapping[str, Any]) -> None:
        level_name = str(event.get("level", "INFO")).upper()
        timestamp_ns = time_ns()
        timestamp = event.get("timestamp")
        if isinstance(timestamp, str):
            timestamp_ns = int(datetime.fromisoformat(timestamp).timestamp() * 1e9)
        self._logger.emit(
            timestamp=timestamp_ns,
            observed_timestamp=time_ns(),
            severity_text=level_name,
            severity_number=_OTLP_SEVERITY.get(level_name, SeverityNumber.INFO),
            body=str(event.get("message", event.get("event", "Telemetry event"))),
            attributes=_flatten_attributes(event),
            event_name=str(event.get("event", "telemetry.event")),
        )

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()


def build_telemetry_sink(
    *,
    environment: str,
    service: str,
    stdout_sink: TelemetrySink | None = None,
    otlp_factory: Callable[[str], TelemetrySink] = OtlpHttpSink,
) -> TelemetrySink:
    stdout = stdout_sink or JsonStdoutSink()
    endpoint = os.getenv(OTLP_LOGS_ENDPOINT_ENV, "").strip()
    if environment != "prod" or not endpoint:
        return stdout
    try:
        otlp = otlp_factory(service)
    except Exception:
        return stdout
    return CompositeSink(stdout, otlp)


def _flatten_attributes(
    value: Mapping[str, Any], prefix: str = ""
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        if not prefix and key == "message":
            # OTLP body owns the human-readable message. Duplicating it as an
            # attribute makes log explorers prefer the raw attribute envelope.
            continue
        path = f"{prefix}.{key}" if prefix else str(key)
        _flatten_attribute_value(flattened, path, item)
    return flattened


def _flatten_attribute_value(
    flattened: dict[str, Any], path: str, value: Any
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _flatten_attribute_value(flattened, f"{path}.{key}", child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _flatten_attribute_value(flattened, f"{path}.{index}", child)
    elif value is not None:
        flattened[path] = value


class InMemorySink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))

    def shutdown(self) -> None:
        return None
