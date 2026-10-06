"""Guide API contracts and working-plan ownership validation."""

from typing import Annotated, Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..trust_boundary import assert_agent_delta_within_boundary, validate_phase_state
from .agent_contract import (
    AgentContent,
    Deduped,
    GeneratedTitle,
    NullAsDefault,
    NullAsEmptyList,
    OptionalText,
    Text,
    case_insensitive,
    number_by_position,
    record_heal,
    relocate_into,
)
from .common import AgentMeta
from .scout import BoundedMessage
from .trip_context import FIXED_KEYS, TripContext


GuidePace = case_insensitive(Literal["relaxed", "balanced", "packed"])
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


class GuideDay(AgentContent):
    day_number: int = Field(ge=1)
    date: OptionalText = None
    places: Annotated[list[Text], NullAsEmptyList, Deduped] = Field(default_factory=list)
    pace: GuidePace
    buffer_note: OptionalText = None


class GuideConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    awaiting: Optional[GuideAwaiting] = None


class GuidePlannerState(BaseModel):
    """Guide-owned working plan state, as currently persisted."""

    model_config = ConfigDict(extra="forbid")

    conversation_context: GuideConversationContext = Field(
        default_factory=GuideConversationContext
    )
    places: list[Text] = Field(default_factory=list)
    day_plan: list[GuideDay] = Field(default_factory=list)


class GuideTripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext = Field(default_factory=TripContext)
    planner_state: GuidePlannerState = Field(default_factory=GuidePlannerState)
    # TWM-232: read-only visibility into the trip's current title so Guide
    # can tell whether one is already set -- the same presence check it
    # already does for any trip_context field -- before deciding whether to
    # generate one. None/the placeholder both read as "not set yet".
    current_title: Optional[str] = None


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
    # A place named twice (even differing only by case) is one place.
    places: Annotated[Optional[list[Text]], Deduped] = None
    day_plan: Optional[list[GuideDay]] = None
    # TWM-232: a short LLM-generated trip title, produced only on the turn
    # Guide clears `awaiting` from "anything_else" and only when
    # `GuideTripState.current_title` was still unset -- the same
    # presence-gated pattern Guide already applies to every trip_context
    # field, not a new turn-type branch. Backend promotes it to the real
    # `Trip.title` column once (see trip_commands/planner_commands.py);
    # Guide itself never checks who set an existing title, only whether one
    # is set.
    generated_title: Annotated[Optional[str], GeneratedTitle] = None

    @field_validator("day_plan", mode="before")
    @classmethod
    def _days_numbered_by_position(cls, value: Any, info: ValidationInfo) -> Any:
        # A day's number is its place in the plan; the later checks that the
        # plan is sequential from 1 then hold by construction.
        numbered, changed = number_by_position(value, "day_number")
        if changed:
            record_heal(info, "day_plan.numbered_by_position")
        return numbered

    @model_validator(mode="before")
    @classmethod
    def _awaiting_belongs_in_conversation_context(cls, data: Any, info: ValidationInfo) -> Any:
        # `awaiting` lives on `conversation_context`; a sibling of it on
        # `planner_state` has exactly one possible destination.
        return relocate_into(data, container="conversation_context", field="awaiting", info=info)


class GuideStateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext = Field(default_factory=TripContext)
    planner_state: GuidePlannerStateDelta = Field(default_factory=GuidePlannerStateDelta)

    @model_validator(mode="after")
    def reject_ui_owned_state(self) -> "GuideStateDelta":
        assert_agent_delta_within_boundary(self)
        return self


GuideOutcome = case_insensitive(Literal["continue", "reopen_destination_discovery"])


class GuideAgentOutput(AgentContent):
    """Canonical generated Guide output before Backend provenance is attached."""

    message: Text
    state_delta: Annotated[GuideStateDelta, NullAsDefault] = Field(default_factory=GuideStateDelta)
    outcome: GuideOutcome = "continue"


class GuideResponse(GuideAgentOutput):
    agent_meta: AgentMeta
