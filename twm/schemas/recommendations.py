"""Typed traveler-criterion recommendation contracts."""

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]
CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
]
ContextPath = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$",
    ),
]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class EstimateRange(BaseModel):
    """Inclusive monetary estimate range in the containing block's currency."""

    model_config = ConfigDict(extra="forbid")

    minimum: Amount
    maximum: Amount

    @model_validator(mode="after")
    def validate_bounds(self) -> "EstimateRange":
        if self.maximum < self.minimum:
            raise ValueError("estimate maximum must be greater than or equal to minimum")
        return self


class BulletDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bullets"]
    items: list[NonEmptyString] = Field(min_length=1)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: NonEmptyString
    value: NonEmptyString


class FactsDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["facts"]
    facts: list[Fact] = Field(min_length=1)


class CostLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: NonEmptyString
    per_person: Optional[EstimateRange] = None
    group: Optional[EstimateRange] = None
    note: Optional[NonEmptyString] = None

    @model_validator(mode="after")
    def validate_estimates(self) -> "CostLineItem":
        if self.per_person is None and self.group is None:
            raise ValueError("cost line item requires a per-person or group estimate")
        _validate_group_not_below_per_person(self.group, self.per_person)
        return self


class CostBreakdownDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cost_breakdown"]
    currency: CurrencyCode
    items: list[CostLineItem] = Field(default_factory=list)
    per_person_total: Optional[EstimateRange] = None
    group_total: Optional[EstimateRange] = None
    note: Optional[NonEmptyString] = None

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
CriterionOutcome = Literal["MATCH", "TRADEOFF", "MISMATCH"]
RequirementType = Literal["HARD", "PREFERENCE"]


class TravelerCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    label: NonEmptyString
    requirement_type: RequirementType
    source_context_paths: list[ContextPath] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_paths(self) -> "TravelerCriterion":
        normalized_paths = [path.casefold() for path in self.source_context_paths]
        if len(set(normalized_paths)) != len(normalized_paths):
            raise ValueError("source context paths must be unique within a criterion")
        return self


class CriterionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: NonEmptyString
    outcome: CriterionOutcome
    conclusion: NonEmptyString
    details: list[RecommendationDetail] = Field(min_length=1)
    tradeoffs: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> "CriterionEvaluation":
        if self.outcome == "MATCH" and self.tradeoffs:
            raise ValueError("a match criterion cannot contain trade-offs")
        if self.outcome in {"TRADEOFF", "MISMATCH"} and not self.tradeoffs:
            raise ValueError("trade-off and mismatch criteria require trade-offs")
        return self


class RecommendationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: Annotated[int, Field(ge=1, le=3)]
    type: Literal["single", "circuit"]
    name: NonEmptyString
    destination_id: Optional[NonEmptyString] = None
    circuit_id: Optional[NonEmptyString] = None
    summary: NonEmptyString
    evaluations: list[CriterionEvaluation] = Field(min_length=1)
    other_considerations: list[NonEmptyString] = Field(default_factory=list)

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

        criterion_ids = [
            evaluation.criterion_id.casefold() for evaluation in self.evaluations
        ]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("criterion evaluations must be unique within an option")

        currencies = {
            detail.currency
            for evaluation in self.evaluations
            for detail in evaluation.details
            if isinstance(detail, CostBreakdownDetail)
        }
        if len(currencies) > 1:
            raise ValueError("cost breakdowns within an option must use one currency")
        return self
