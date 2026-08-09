"""A test adapter that always returns one recorded fixture's raw output."""

from twm.services.agent_engine.contracts import (
    AgentInvocation,
    AgentInvocationResult,
    AgentName,
)


class RecordedAdapter:
    """AgentAdapter implementation replaying a single recorded completion."""

    def __init__(self, raw_output: str) -> None:
        self._raw_output = raw_output

    async def invoke(
        self, agent: AgentName, invocation: AgentInvocation
    ) -> AgentInvocationResult:
        return AgentInvocationResult(raw_output=self._raw_output, metadata={})
