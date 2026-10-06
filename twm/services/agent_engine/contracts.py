"""Engine-neutral execution contracts."""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, Protocol

from ...prompt_registry import PromptRelease


AgentName = Literal["scout", "meridian", "guide", "atlas"]

# A Backend business rule applied to an agent's schema-valid output before it
# is accepted: returns the plain-language rules it breaks (empty when fine).
# A violation is retried like any other contract failure -- the model is told
# the rule -- instead of surfacing to the traveler after the engine returned.
OutputReview = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class GenerationConfig:
    """Provider-neutral limits applied to every agent invocation."""

    max_output_tokens: int = 16_384
    temperature: float = 0.2
    timeout_seconds: int = 180


@dataclass(frozen=True)
class OutputRetryPolicy:
    """How a response that breaks the output contract is regenerated.

    Every attempt is a full generation the traveler waits for, so there is
    exactly one retry: the answer to a contract that fails often is to prevent
    the failure (heal it, derive what Backend can compute, tolerate the syntax
    slip), never to retry more. The retry tells the model which rules the
    previous attempt broke, and starts only while it is expected to finish
    inside `budget_seconds` (judged by how long the first took), so a slow
    upstream cannot push the turn past the UI's own request timeout.
    """

    max_attempts: int = 2
    budget_seconds: float = 190.0

    def allows_another(self, attempt: int, elapsed_seconds: float, last_attempt_seconds: float) -> bool:
        return attempt < self.max_attempts and elapsed_seconds + last_attempt_seconds <= self.budget_seconds


@dataclass(frozen=True)
class AgentInvocation:
    """Provider-neutral model input prepared by the common Backend pipeline."""

    system_prompt: str
    user_prompt: str
    generation: GenerationConfig = field(default_factory=GenerationConfig)


@dataclass(frozen=True)
class AgentInvocationResult:
    """Serialized generated output plus telemetry exposed by the selected engine."""

    raw_output: str
    metadata: dict[str, str | int | float] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentExecution:
    response: dict[str, Any]
    prompt_release: PromptRelease


class AgentAdapter(Protocol):
    """Invoke one engine and return generated output for common validation."""

    async def invoke(
        self, agent: AgentName, invocation: AgentInvocation
    ) -> AgentInvocationResult:
        ...


class AgentEngine(Protocol):
    async def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...

    async def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...

    async def guide(
        self,
        trip_state: dict[str, Any],
        message: Optional[str],
        review: Optional[OutputReview] = None,
    ) -> AgentExecution:
        ...

    async def atlas(
        self,
        trip_state: dict[str, Any],
        message: Optional[str],
        review: Optional[OutputReview] = None,
    ) -> AgentExecution:
        ...


class AgentAdapterError(RuntimeError):
    """The selected engine failed before yielding a usable completion."""

    def __init__(
        self,
        message: str,
        *,
        component: str = "agent_engine",
        failure_stage: str = "invocation",
        error_type: str | None = None,
        detail: str | None = None,
        upstream_status_code: int | None = None,
        upstream_response: Any = None,
    ) -> None:
        super().__init__(message)
        self.component = component
        self.failure_stage = failure_stage
        self.error_type = error_type or type(self).__name__
        self.detail = detail or message
        self.upstream_status_code = upstream_status_code
        self.upstream_response = upstream_response


class AgentAdapterTimeoutError(AgentAdapterError):
    """The selected engine exceeded its configured invocation timeout."""


class AgentOutputError(RuntimeError):
    """The model output failed schema validation."""

    def __init__(self, agent: AgentName, failures: list[dict[str, Any]]) -> None:
        super().__init__(f"{agent} returned invalid output")
        self.agent = agent
        self.failures = failures
