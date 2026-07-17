"""Meridian-specific structured model output."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ....schemas.meridian import MeridianStateDelta, MeridianStatus


class MeridianModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: MeridianStatus
    state_delta: MeridianStateDelta = Field(default_factory=MeridianStateDelta)
    message: Optional[str] = None
    generated_at: Optional[str] = None
    trip_type: Optional[Literal["single", "circuit", "mixed"]] = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    constraint_adjustment_suggestions: Optional[list[str]] = None
