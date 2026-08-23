"""Engine-neutral execution contracts."""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

from ...prompt_registry import PromptRelease


AgentName = Literal["scout", "meridian", "guide", "atlas"]


@dataclass(frozen=True)
class GenerationConfig:
    """Provider-neutral limits applied to every agent invocation."""

    max_output_tokens: int = 16_384
    temperature: float = 0.2
    timeout_seconds: int = 180


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

    async def invoke_raw(self, invocation: AgentInvocation) -> AgentInvocationResult:
        """Invoke the engine for a small, non-trip_state, non-AgentName caller.

        TWM-195's Trusted Actions route-mode classifier is the first caller:
        it needs a single structured system/user prompt turn and raw text
        back, without being shoehorned into the scout/meridian/guide/atlas
        AgentName dispatch (which is trip_state-shaped and versioned by
        ``twm/prompt_registry.py``, neither of which applies to this small
        internal helper). Both engines implement this the same shape as
        ``invoke`` — a raw completion, no schema validation — so
        higher-level callers (e.g. the route classifier) own their own
        strict parsing instead of reusing ``AgentExecutionService``.
        """
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
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...

    async def atlas(
        self, trip_state: dict[str, Any], message: Optional[str]
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
