"""Request shape for the trip-feasibility assessment boundary (TWM-131).

Kept in a separate module from ``twm/schemas/trusted_action.py`` (which
TWM-131 must not modify without a proven contract defect) since
``TripFeasibilityAssessment`` has no natural request counterpart there:
feasibility is a route-level judgement (flight/train/bus/drive for an
origin/destination pair), not scoped to a single action_type/domain/partner
the way ``TrustedActionRequest`` is. See
``twm/routers/trusted_action.py`` for why this is exposed as its own
endpoint rather than folded into the trusted-action resolution response.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .trusted_action import TrustedActionText


class TripFeasibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: TrustedActionText
    destination: TrustedActionText
    # TWM-215: Atlas's ballpark distance for a gateway-hub pair, sent when the
    # caller has substituted a candidate hub for a hubless leg endpoint. Used
    # only as a fallback when the bundled resolver cannot place a city (a
    # rail-only hub with no airport); ignored when both cities resolve.
    long_haul_distance_km: Optional[float] = Field(default=None, gt=0)
