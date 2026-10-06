"""Typed traveler-criterion recommendation contracts."""

from typing import Annotated, Literal, Optional, Union

from pydantic import Field, StringConstraints, ValidationInfo, model_validator

from .agent_contract import (
    AgentContent,
    LenientNumber,
    NullAsEmptyList,
    OptionalText,
    Text,
    UpperCode,
    case_insensitive,
    ordered_range,
    ensure_unique,
)


CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
    UpperCode,
]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False), LenientNumber]


class EstimateRange(AgentContent):
    """Inclusive monetary estimate range in the containing block's currency."""

    minimum: Amount
    maximum: Amount

    @model_validator(mode="after")
    def validate_bounds(self, info: ValidationInfo) -> "EstimateRange":
        self.minimum, self.maximum = ordered_range(self.minimum, self.maximum, info, "estimate")
        return self


class BulletDetail(AgentContent):
    type: Literal["bullets"]
    items: list[Text] = Field(min_length=1)


class Fact(AgentContent):
    label: Text
    value: Text


class FactsDetail(AgentContent):
    type: Literal["facts"]
    facts: list[Fact] = Field(min_length=1)


class CostLineItem(AgentContent):
    label: Text
    per_person: Optional[EstimateRange] = None
    group: Optional[EstimateRange] = None
    note: OptionalText = None

    @model_validator(mode="after")
    def validate_estimates(self) -> "CostLineItem":
        if self.per_person is None and self.group is None:
            raise ValueError("cost line item requires a per-person or group estimate")
        _validate_group_not_below_per_person(self.group, self.per_person)
        return self


class CostBreakdownDetail(AgentContent):
    type: Literal["cost_breakdown"]
    currency: CurrencyCode
    items: list[CostLineItem] = Field(default_factory=list)
    per_person_total: Optional[EstimateRange] = None
    group_total: Optional[EstimateRange] = None
    note: OptionalText = None

    @model_validator(mode="after")
    def validate_totals(self) -> "CostBreakdownDetail":
        if (
            not self.items
            and self.per_person_total is None
            and self.group_total is None
        ):
            raise ValueError("cost breakdown requires at least one numeric estimate")
        _validate_group_not_below_per_person(
            self.group_total, self.per_person_total
        )
        return self


def _validate_group_not_below_per_person(
    group: Optional[EstimateRange], per_person: Optional[EstimateRange]
) -> None:
    if group is None or per_person is None:
        return
    if (
        group.minimum < per_person.minimum
        or group.maximum < per_person.maximum
    ):
        raise ValueError("group estimate cannot be lower than per-person estimate")


RecommendationDetail = Annotated[
    Union[BulletDetail, FactsDetail, CostBreakdownDetail],
    Field(discriminator="type"),
]
CriterionOutcome = case_insensitive(Literal["MATCH", "TRADEOFF", "MISMATCH"])
RequirementType = case_insensitive(Literal["HARD", "PREFERENCE"])


class TravelerCriterion(AgentContent):
    """One material traveler ask. Provenance (which TripContext fields fed it)
    is deliberately not part of the contract: nothing consumes it, and a single
    free-text field routinely carries several asks."""

    id: Text
    label: Text
    requirement_type: RequirementType


class CriterionEvaluation(AgentContent):
    criterion_id: Text
    outcome: CriterionOutcome
    conclusion: Text
    details: list[RecommendationDetail] = Field(min_length=1)
    tradeoffs: Annotated[list[Text], NullAsEmptyList] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> "CriterionEvaluation":
        if self.outcome == "MATCH" and self.tradeoffs:
            raise ValueError("a match criterion cannot contain trade-offs")
        if self.outcome in {"TRADEOFF", "MISMATCH"} and not self.tradeoffs:
            raise ValueError("trade-off and mismatch criteria require trade-offs")
        return self


class RecommendationOption(AgentContent):
    rank: Annotated[int, Field(ge=1, le=3)]
    type: case_insensitive(Literal["single", "circuit"])
    name: Text
    destination_id: OptionalText = None
    circuit_id: OptionalText = None
    summary: Text
    evaluations: list[CriterionEvaluation] = Field(min_length=1)
    other_considerations: Annotated[list[Text], NullAsEmptyList] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity_and_evaluations(self) -> "RecommendationOption":
        if self.type == "single":
            if self.destination_id is None or self.circuit_id is not None:
                raise ValueError(
                    "a single option requires destination_id and forbids circuit_id"
                )
        elif self.circuit_id is None or self.destination_id is not None:
            raise ValueError(
                "a circuit option requires circuit_id and forbids destination_id"
            )

        ensure_unique(
            [evaluation.criterion_id for evaluation in self.evaluations],
            "criterion evaluations within an option",
        )

        currencies = {
            detail.currency
            for evaluation in self.evaluations
            for detail in evaluation.details
            if isinstance(detail, CostBreakdownDetail)
        }
        if len(currencies) > 1:
            raise ValueError("cost breakdowns within an option must use one currency")
        return self
