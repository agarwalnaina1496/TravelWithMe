"""Stable application-facing telemetry facade."""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .context import get_correlation_context
from .sanitization import payload_metadata, sanitize
from .settings import PayloadMode, TelemetrySettings
from .sinks import TelemetrySink


SCHEMA_VERSION = "1.0"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class TelemetryLogger:
    """Build the versioned envelope and isolate all sink failures."""

    def __init__(
        self,
        settings: TelemetrySettings,
        sink: TelemetrySink,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._sink = sink
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def event(
        self,
        name: str,
        *,
        level: str = "INFO",
        source: str = "application",
        fields: Mapping[str, Any] | None = None,
        payload: Any = None,
    ) -> None:
        """Compatibility entry point for internal telemetry events."""
        self._log(
            name,
            event=name,
            level=level,
            source=source,
            fields=fields,
            payload=payload,
        )

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(message, level="DEBUG", **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log(message, level="INFO", **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(message, level="WARNING", **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._log(message, level="ERROR", **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        self._log(message, level="CRITICAL", **kwargs)

    def _log(
        self,
        message: str,
        *,
        event: str,
        level: str,
        source: str = "application",
        fields: Mapping[str, Any] | None = None,
        payload: Any = None,
        response: Any = None,
        **structured_fields: Any,
    ) -> None:
        if not self.settings.enabled:
            return
        try:
            normalized_level = level.upper()
            if normalized_level not in _LEVELS:
                raise ValueError(f"Unsupported telemetry level: {level}")
            context = get_correlation_context()
            envelope: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "timestamp": self._clock().astimezone(timezone.utc).isoformat(),
                "level": normalized_level,
                "environment": self.settings.environment,
                "service": self.settings.service,
                "source": source,
                "event": event,
                "message": message,
                "request_id": context.request_id if context else str(uuid4()),
            }
            if context and context.trip_id:
                envelope["trip_id"] = context.trip_id
            if context and context.turn_id:
                envelope["turn_id"] = context.turn_id
            combined_fields = dict(fields or {})
            combined_fields.update(structured_fields)
            if combined_fields:
                envelope["fields"] = combined_fields
            self._add_diagnostic(envelope, "payload", payload)
            self._add_diagnostic(envelope, "response", response)

            safe_event = sanitize(envelope, self.settings.max_field_size)
            self._sink.emit(safe_event)
        except Exception:
            # Observability must never alter traveler-facing behavior.
            return

    def _add_diagnostic(
        self, envelope: dict[str, Any], name: str, value: Any
    ) -> None:
        if value is None or self.settings.payload_mode is PayloadMode.OFF:
            return
        if self.settings.payload_mode is PayloadMode.METADATA:
            envelope[f"{name}_metadata"] = payload_metadata(value)
        elif self.settings.payload_mode is PayloadMode.FULL:
            envelope[name] = value

    def shutdown(self) -> None:
        try:
            self._sink.shutdown()
        except Exception:
            return
