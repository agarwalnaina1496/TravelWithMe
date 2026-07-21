"""Replaceable telemetry delivery boundaries."""

from collections.abc import Mapping
import json
import sys
from typing import Any, Protocol, TextIO


class TelemetrySink(Protocol):
    def emit(self, event: Mapping[str, Any]) -> None: ...


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


class InMemorySink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))
