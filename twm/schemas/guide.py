"""Guide API contracts and working-plan ownership validation."""

from typing import Annotated, Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..trust_boundary import validate_phase_state
from .common import AgentMeta
from .scout import BoundedMessage
from .trip_context import FIXED_KEYS, TripContext


GuideText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
GuidePace = Literal["relaxed", "balanced", "packed"]
# No "is this the first message" distinction — Guide's job is identical
# every turn (extract whatever the message contains, check the gates in
# order, ask the next missing one or generate the plan once all are known),
# whether this is a trip's very first Guide call or its fiftieth. The only
# genuinely different event is APPROVE_PLAN, which Guide never even
# receives — Backend applies it deterministically (see apply_guide).
GuideEvent = Literal["MESSAGE", "APPROVE_PLAN"]
# Fixed trip-context inputs Guide gates on before a plan can be built. Kept
# as a small enum (like Meridian's `awaiting`) rather than free text so the
# UI can drive it with a fixed quick-reply set. Each of the five slugs is
# the exact matching trip_context key name (derived from TripContext's own
# FIXED_KEYS so the two can't drift) — values stay verbatim under that key.
# `anything_else` is the sixth, open gating question asked after all five
# fixed fields are known; its answer is extracted like any other turn under
# a freely chosen semantic key, never a fixed key of its own.
GuideAwaiting = Literal[(*FIXED_KEYS, "anything_else")]


class GuideDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1)
    date: Optional[str] = None
    places: list[GuideText] = Field(default_factory=list)
    pace: GuidePace
    buffer_note: Optional[GuideText] = None


class GuideConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    awaiting: Optional[GuideAwaiting] = None


class GuidePlannerState(BaseModel):
    """Guide-owned working plan state, as currently persisted."""

    model_config = ConfigDict(extra="forbid")

    conversation_context: GuideConversationContext = Field(
        default_factory=GuideConversationContext
    )
    places: list[GuideText] = Field(default_factory=list)
    day_plan: list[GuideDay] = Field(default_factory=list)


class GuideTripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext = Field(default_factory=TripContext)
    planner_state: GuidePlannerState = Field(default_factory=GuidePlannerState)


class GuideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: GuideEvent = "MESSAGE"
    trip_state: GuideTripState = Field(default_factory=GuideTripState)
    # Optional: absent on the cold Discover-path transition (a destination
    # was just selected via select_destination, nothing for the traveler to
    # say yet) — Guide simply checks the gates and asks the first missing
    # one. Present on every other MESSAGE turn.
    message: Optional[BoundedMessage] = None

    @model_validator(mode="after")
    def validate_request(self) -> "GuideRequest":
        if self.message is not None and not self.message.strip():
            raise ValueError("message must not be blank when provided")
        validate_phase_state(self.trip_state.model_dump())
        return self


class GuidePlannerStateDelta(BaseModel):
    """Only the planner_state fields Guide is intentionally changing this
    turn. An omitted field means Backend keeps the existing value —
    mirrors Scout/Meridian's state_delta pattern instead of requiring a
    full-state echo of anything left untouched."""

    model_config = ConfigDict(extra="forbid")

    conversation_context: Optional[GuideConversationContext] = None
    places: Optional[list[GuideText]] = None
    day_plan: Optional[list[GuideDay]] = None

    @model_validator(mode="after")
    def validate_places_unique(self) -> "GuidePlannerStateDelta":
        if self.places is not None:
            normalized = [place.casefold() for place in self.places]
            if len(normalized) != len(set(normalized)):
                raise ValueError("places must be unique")
        return self


class GuideStateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext = Field(default_factory=TripContext)
    planner_state: GuidePlannerStateDelta = Field(default_factory=GuidePlannerStateDelta)

    @model_validator(mode="after")
    def reject_ui_owned_state(self) -> "GuideStateDelta":
        if "selected_option" in (self.trip_context.model_extra or {}):
            raise ValueError("selected_option is UI-owned")
        return self


GuideOutcome = Literal["continue", "reopen_destination_discovery"]


class GuideAgentOutput(BaseModel):
    """Canonical generated Guide output before Backend provenance is attached."""

    model_config = ConfigDict(extra="forbid")

    message: GuideText
    state_delta: GuideStateDelta = Field(default_factory=GuideStateDelta)
    outcome: GuideOutcome = "continue"


class GuideResponse(GuideAgentOutput):
    agent_meta: AgentMeta
