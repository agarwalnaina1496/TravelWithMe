"""Scout-specific structured model output."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ....schemas.scout import ScoutStateDelta


class ScoutModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: Optional[str] = None
    state_delta: ScoutStateDelta = Field(default_factory=ScoutStateDelta)
    intent: Optional[Literal["advise", "matcher", "planner"]] = None
