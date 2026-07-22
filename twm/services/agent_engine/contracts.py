"""Engine-neutral execution contracts."""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

from ...prompts import PromptRelease


AgentName = Literal["scout", "meridian"]


@dataclass(frozen=True)
class AgentInvocation:
    """Provider-neutral model input prepared by the common Backend pipeline."""

    system_prompt: str
    user_prompt: str


@dataclass(frozen=True)
class AgentInvocationResult:
    """Raw completion plus provider telemetry exposed by the selected engine."""

    raw_output: str
    metadata: dict[str, str | int | float] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentExecution:
    response: dict[str, Any]
    prompt_release: PromptRelease


class AgentAdapter(Protocol):
    """Invoke one engine and return its unparsed model completion."""

    @property
    def engine_name(self) -> str:
        ...

    def endpoint(self, agent: AgentName) -> str | None:
        """Return a safe logical endpoint identifier for telemetry events."""
        ...

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


class AgentAdapterError(RuntimeError):
    """The selected engine failed before yielding a usable completion."""


class AgentAdapterTimeoutError(AgentAdapterError):
    """The selected engine exceeded its configured invocation timeout."""


class AgentOutputError(RuntimeError):
    """The model output remained invalid after the bounded repair attempt."""

    def __init__(self, agent: AgentName, failures: list[dict[str, Any]]) -> None:
        super().__init__(f"{agent} returned invalid output")
        self.agent = agent
        self.failures = failures
