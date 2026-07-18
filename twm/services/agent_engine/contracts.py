"""Engine-neutral execution contracts."""

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ...prompts import PromptRelease


@dataclass(frozen=True)
class AgentExecution:
    response: dict[str, Any]
    prompt_release: PromptRelease


class AgentEngine(Protocol):
    async def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...

    async def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...
