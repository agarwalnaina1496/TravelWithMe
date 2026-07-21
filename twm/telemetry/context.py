"""Request-scoped telemetry correlation without business-model dependencies."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
import re
from uuid import uuid4


_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class CorrelationContext:
    request_id: str
    trip_id: str | None = None
    turn_id: str | None = None


_current_context: ContextVar[CorrelationContext | None] = ContextVar(
    "twm_telemetry_context", default=None
)


def valid_correlation_id(value: str | None) -> str | None:
    """Return a safe interoperable identifier, or None when it is invalid."""
    if value is None or not _CORRELATION_ID.fullmatch(value):
        return None
    return value


def build_correlation_context(
    request_id: str | None,
    trip_id: str | None,
    turn_id: str | None,
) -> CorrelationContext:
    return CorrelationContext(
        request_id=valid_correlation_id(request_id) or str(uuid4()),
        trip_id=valid_correlation_id(trip_id),
        turn_id=valid_correlation_id(turn_id),
    )


def get_correlation_context() -> CorrelationContext | None:
    return _current_context.get()


def set_correlation_context(context: CorrelationContext) -> Token:
    return _current_context.set(context)


def reset_correlation_context(token: Token) -> None:
    _current_context.reset(token)
