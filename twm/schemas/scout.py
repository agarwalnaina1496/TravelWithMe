"""Scout API contracts."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta


class ScoutRequest(BaseModel):
    trip_state: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class ScoutStateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_ui_owned_state(self) -> "ScoutStateDelta":
        if "selected_option" in self.trip_context:
            raise ValueError("selected_option is UI-owned")
        return self


class ScoutAgentOutput(BaseModel):
    """Structured Scout output before Backend-owned provenance is attached."""

    model_config = ConfigDict(extra="forbid")

    message: Optional[str] = None
    state_delta: ScoutStateDelta = Field(default_factory=ScoutStateDelta)
    intent: Optional[Literal["advise", "matcher", "planner"]] = None


class ScoutResponse(ScoutAgentOutput):
    """Public Scout response with deterministic Backend provenance."""

    agent_meta: AgentMeta
