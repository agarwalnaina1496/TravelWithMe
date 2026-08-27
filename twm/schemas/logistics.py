"""Application-owned confirmed logistics anchors."""

from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

LogisticsText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
LogisticsAnchorType = Literal["transport", "stay", "activity"]


class LogisticsConfirmationInput(BaseModel):
    """Manual structured confirmation supplied by the traveler (no file upload)."""

    model_config = ConfigDict(extra="forbid")

    type: LogisticsAnchorType
    label: LogisticsText
    detail: LogisticsText
    day_number: Optional[int] = Field(default=None, ge=1)
    reference: Optional[LogisticsText] = None
    notes: Optional[LogisticsText] = None
    # TWM-198/TWM-209: the confirming caller's TripBoardItem.id, when known —
    # lets readiness matching (bookingReadinessRollup) identify the exact
    # item this anchor confirms instead of falling back to day/type
    # matching. Optional and additive: no existing caller is required to
    # send it, and a day_number-only anchor (including all pre-existing
    # anchor data) remains valid, just without the tighter match.
    board_item_id: Optional[LogisticsText] = None


class LogisticsAnchor(LogisticsConfirmationInput):
    """Persisted, owner-isolated confirmed logistics anchor."""

    id: UUID
    confirmed_at_version: int = Field(ge=1)
