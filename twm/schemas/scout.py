"""Scout API contracts."""

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .common import AgentMeta
from ..security import MAX_MESSAGE_CHARACTERS, validate_phase_state


BoundedMessage = Annotated[str, StringConstraints(max_length=MAX_MESSAGE_CHARACTERS)]


class ScoutAdvisorConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_advisor_message: Optional[str] = None


class ScoutAdvisorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_context: ScoutAdvisorConversationContext = Field(
        default_factory=ScoutAdvisorConversationContext
    )


ScoutStage = Literal[
    "new",
    "matching",
    "recommendation_ready",
    "recommended",
    "matched",
    "planning",
    "planned",
]


class ScoutTripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ScoutStage = "new"
    trip_context: dict[str, Any] = Field(default_factory=dict)
    advisor_state: ScoutAdvisorState = Field(default_factory=ScoutAdvisorState)


class ScoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_state: ScoutTripState = Field(default_factory=ScoutTripState)
    message: Optional[BoundedMessage] = None

    @model_validator(mode="after")
    def validate_untrusted_state(self) -> "ScoutRequest":
        validate_phase_state(self.trip_state.model_dump())
        return self


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
