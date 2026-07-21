"""Vendor-neutral Travel With Me telemetry foundation."""

from .context import (
    CorrelationContext,
    build_correlation_context,
    get_correlation_context,
    reset_correlation_context,
    set_correlation_context,
    valid_correlation_id,
)
from .logger import SCHEMA_VERSION, TelemetryLogger
from .middleware import (
    CORRELATION_HEADERS,
    REQUEST_ID_HEADER,
    TRIP_ID_HEADER,
    TURN_ID_HEADER,
    TelemetryContextMiddleware,
)
from .settings import PayloadMode, TelemetrySettings
from .sinks import (
    CompositeSink,
    InMemorySink,
    JsonStdoutSink,
    OtlpHttpSink,
    TelemetrySink,
    build_telemetry_sink,
)

__all__ = [
    "CORRELATION_HEADERS", "CompositeSink", "CorrelationContext", "InMemorySink",
    "JsonStdoutSink", "OtlpHttpSink", "PayloadMode", "REQUEST_ID_HEADER", "SCHEMA_VERSION",
    "TRIP_ID_HEADER", "TURN_ID_HEADER", "TelemetryContextMiddleware",
    "TelemetryLogger", "TelemetrySettings", "TelemetrySink",
    "build_correlation_context", "build_telemetry_sink", "get_correlation_context",
    "reset_correlation_context", "set_correlation_context",
    "valid_correlation_id",
]
