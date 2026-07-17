"""Meridian API contracts and ownership validation."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta
from .recommendations import NonEmptyString, RecommendationOption


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

    trip_context: dict[str, Any] = Field(default_factory=dict)
    advisor_state: MeridianAdvisorState = Field(default_factory=MeridianAdvisorState)
    matcher_state: dict[str, Any] = Field(default_factory=dict)


class MeridianRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_state: MeridianTripState = Field(default_factory=MeridianTripState)
    message: Optional[str] = None


class MeridianStateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: dict[str, Any] = Field(default_factory=dict)
    matcher_state: dict[str, Any] = Field(default_factory=dict)

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
    message: NonEmptyString
    generated_at: Optional[str] = None
    trip_type: Optional[Literal["single", "circuit", "mixed"]] = None
    options: list[RecommendationOption] = Field(default_factory=list, max_length=3)
    constraint_adjustment_suggestions: Optional[list[NonEmptyString]] = None
    agent_meta: AgentMeta

    @model_validator(mode="after")
    def validate_outcome(self) -> "MeridianResponse":
        self._validate_status_options()
        self._validate_ranks()
        self._validate_option_consistency()
        self._validate_conversation_state()
        self._validate_soft_fail_tradeoffs()
        self._validate_constraint_adjustments()
        return self

    def _validate_status_options(self) -> None:
        if self.status in {"SUCCESS", "SOFT_FAIL"}:
            if not self.options:
                raise ValueError(f"{self.status} requires at least one valid option")
            return
        if self.options:
            raise ValueError(f"{self.status} does not allow recommendation options")

    def _validate_ranks(self) -> None:
        ranks = [option.rank for option in self.options]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("option ranks must be unique and sequential from 1")

    def _validate_option_consistency(self) -> None:
        if not self.options:
            return

        identities = [
            (option.type, option.destination_id or option.circuit_id)
            for option in self.options
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("recommendation option identities must be unique")

        expected_criteria = {
            criterion.id.casefold(): (
                criterion.label.casefold(),
                criterion.requirement_type,
            )
            for criterion in self.options[0].criteria
        }
        for option in self.options[1:]:
            option_criteria = {
                criterion.id.casefold(): (
                    criterion.label.casefold(),
                    criterion.requirement_type,
                )
                for criterion in option.criteria
            }
            if option_criteria != expected_criteria:
                raise ValueError(
                    "every option must evaluate the same traveler criteria"
                )

        option_types = {option.type for option in self.options}
        if self.trip_type in {"single", "circuit"} and option_types != {
            self.trip_type
        }:
            raise ValueError("trip_type must match every recommendation option")
        if self.trip_type == "mixed" and option_types != {"single", "circuit"}:
            raise ValueError("mixed trip_type requires single and circuit options")

    def _validate_conversation_state(self) -> None:
        context = self.state_delta.matcher_state.get("conversation_context")
        if not isinstance(context, dict):
            raise ValueError("matcher conversation_context is required")

        if self.status == "NEEDS_CLARIFICATION":
            awaiting = context.get("awaiting")
            if not isinstance(awaiting, str) or not awaiting.strip():
                raise ValueError(
                    "NEEDS_CLARIFICATION requires one non-empty awaiting value"
                )
            if context.get("last_meridian_message") != self.message:
                raise ValueError(
                    "clarification message must match last_meridian_message"
                )
            return

        if "awaiting" not in context or context["awaiting"] is not None:
            raise ValueError("terminal outcomes must clear awaiting state")

    def _validate_soft_fail_tradeoffs(self) -> None:
        if self.status != "SOFT_FAIL":
            return
        for option in self.options:
            has_criterion_tradeoff = any(
                criterion.outcome in {"TRADEOFF", "MISMATCH"}
                for criterion in option.criteria
            )
            if not option.tradeoffs and not has_criterion_tradeoff:
                raise ValueError("every SOFT_FAIL option requires a visible trade-off")

    def _validate_constraint_adjustments(self) -> None:
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
            if not suggestions:
                raise ValueError(
                    "constraint_adjustment_suggestions must contain useful non-empty suggestions"
                )
