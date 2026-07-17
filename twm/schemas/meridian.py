"""Meridian API contracts and ownership validation."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta
from .recommendations import NonEmptyString, RecommendationOption, TravelerCriterion


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
    traveler_criteria: Optional[list[TravelerCriterion]] = None
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
            if not self.traveler_criteria:
                raise ValueError(f"{self.status} requires traveler criteria")
            if not self.options:
                raise ValueError(f"{self.status} requires at least one valid option")
            return
        if self.traveler_criteria is not None:
            raise ValueError(f"{self.status} does not allow traveler criteria")
        if self.options:
            raise ValueError(f"{self.status} does not allow recommendation options")

    def _validate_ranks(self) -> None:
        ranks = [option.rank for option in self.options]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("option ranks must be unique and sequential from 1")

    def _validate_option_consistency(self) -> None:
        if not self.options:
            return

        catalog = self.traveler_criteria or []
        catalog_ids = [criterion.id.casefold() for criterion in catalog]
        if len(set(catalog_ids)) != len(catalog_ids):
            raise ValueError("traveler criterion ids must be unique")

        catalog_labels = [
            criterion.label.casefold() for criterion in catalog
        ]
        if len(set(catalog_labels)) != len(catalog_labels):
            raise ValueError("traveler criterion labels must be unique")

        source_paths = [
            path.casefold()
            for criterion in catalog
            for path in criterion.source_context_paths
        ]
        if len(set(source_paths)) != len(source_paths):
            raise ValueError(
                "a source context path cannot belong to multiple criteria"
            )

        criteria_by_id = {
            criterion.id.casefold(): criterion for criterion in catalog
        }

        identities = [
            (option.type, option.destination_id or option.circuit_id)
            for option in self.options
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("recommendation option identities must be unique")

        expected_criteria = set(criteria_by_id)
        for option in self.options:
            option_criteria = {
                evaluation.criterion_id.casefold()
                for evaluation in option.evaluations
            }
            if option_criteria != expected_criteria:
                raise ValueError(
                    "every option must evaluate every traveler criterion exactly once"
                )
            for evaluation in option.evaluations:
                criterion = criteria_by_id[evaluation.criterion_id.casefold()]
                if (
                    criterion.requirement_type == "HARD"
                    and evaluation.outcome == "MISMATCH"
                ):
                    raise ValueError(
                        "a hard requirement cannot have a mismatch outcome"
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
                evaluation.outcome in {"TRADEOFF", "MISMATCH"}
                for evaluation in option.evaluations
            )
            if not option.other_considerations and not has_criterion_tradeoff:
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
