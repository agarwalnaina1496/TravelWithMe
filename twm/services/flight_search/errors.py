"""Flight-search provider adapter error contracts (TWM-145).

Deliberately parallel to (not imported from) twm/services/agent_engine's
AgentAdapterError/AgentAdapterTimeoutError — flight search is a separate
integration boundary. Unlike the agent-engine errors, these never carry an
upstream_response/raw-payload field: TWM-144's contract forbids raw
provider payloads leaking anywhere, including exception objects that might
be logged.
"""

from typing import Optional


class FlightProviderError(RuntimeError):
    """The flight-search provider failed before yielding usable offer data."""

    def __init__(
        self,
        message: str,
        *,
        component: str = "aviasales",
        failure_stage: str = "invocation",
        error_type: Optional[str] = None,
        detail: Optional[str] = None,
        upstream_status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.component = component
        self.failure_stage = failure_stage
        self.error_type = error_type or type(self).__name__
        self.detail = detail or message
        self.upstream_status_code = upstream_status_code


class FlightProviderTimeoutError(FlightProviderError):
    """The flight-search provider exceeded its configured request timeout."""
