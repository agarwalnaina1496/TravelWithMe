"""Meridian-specific structured model output."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ....schemas.meridian import MeridianStateDelta, MeridianStatus
from ....schemas.recommendations import (
    NonEmptyString,
    RecommendationOption,
    TravelerCriterion,
)


class MeridianModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MeridianStatus
    state_delta: MeridianStateDelta = Field(default_factory=MeridianStateDelta)
    message: NonEmptyString
    generated_at: Optional[str] = None
    trip_type: Optional[Literal["single", "circuit", "mixed"]] = None
    criteria_catalog: Optional[list[TravelerCriterion]] = None
    options: list[RecommendationOption] = Field(default_factory=list, max_length=3)
    constraint_adjustment_suggestions: Optional[list[NonEmptyString]] = None
