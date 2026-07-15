from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScoutRequest(BaseModel):
    trip_state: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class MeridianAdvisorConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_advisor_message: Optional[str] = None


class MeridianAdvisorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_context: MeridianAdvisorConversationContext = Field(
        default_factory=MeridianAdvisorConversationContext
    )


class MeridianTripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: Dict[str, Any] = Field(default_factory=dict)
    advisor_state: MeridianAdvisorState = Field(default_factory=MeridianAdvisorState)
    matcher_state: Dict[str, Any] = Field(default_factory=dict)


class MeridianRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_state: MeridianTripState = Field(default_factory=MeridianTripState)
    message: Optional[str] = None


class AgentMeta(BaseModel):
    agent: Literal["scout", "meridian"]
    prompt_version: str


class ScoutResponse(BaseModel):
    message: Optional[str] = None
    state_delta: Dict[str, Any]
    intent: Optional[str] = None
    agent_meta: AgentMeta


class MeridianStateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: Dict[str, Any] = Field(default_factory=dict)
    matcher_state: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_ui_owned_state(self) -> "MeridianStateDelta":
        if "selected_option" in self.trip_context:
            raise ValueError("selected_option is UI-owned")
        if "recommendations" in self.matcher_state:
            raise ValueError("recommendation history is UI-owned")
        return self


MeridianStatus = Literal[
    "NEEDS_CLARIFICATION",
    "SUCCESS",
    "SOFT_FAIL",
    "HARD_FAIL",
    "BUDGET_FAIL",
    "CONFLICT_FAIL",
]


class MeridianResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MeridianStatus
    state_delta: MeridianStateDelta = Field(default_factory=MeridianStateDelta)
    message: Optional[str] = None
    generated_at: Optional[str] = None
    trip_type: Optional[Literal["single", "circuit", "mixed"]] = None
    options: List[Dict[str, Any]] = Field(default_factory=list)
    constraint_adjustment_suggestions: Optional[List[str]] = None
    agent_meta: AgentMeta

    @model_validator(mode="after")
    def validate_constraint_adjustments(self) -> "MeridianResponse":
        suggestions = self.constraint_adjustment_suggestions
        allowed_statuses = {
            "SOFT_FAIL",
            "HARD_FAIL",
            "BUDGET_FAIL",
            "CONFLICT_FAIL",
        }
        if suggestions is not None:
            if self.status not in allowed_statuses:
                raise ValueError(
                    "constraint_adjustment_suggestions is allowed only for failure outcomes"
                )
            if not suggestions or any(not item.strip() for item in suggestions):
                raise ValueError(
                    "constraint_adjustment_suggestions must contain useful non-empty suggestions"
                )
        return self
