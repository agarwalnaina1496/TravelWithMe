"""Batch booking-options contract (TWM-220).

``POST /trips/{id}/booking-options`` resolves several trusted actions for one
open booking drawer in a single request, instead of the client fanning out
3-4 ``POST /trips/{id}/trusted-action`` calls. It is a pure server-side
fan-out: every target is resolved through the exact same
``TrustedActionService.resolve`` path used by the single-action endpoint, and
one target's non-``resolved`` outcome never fails the batch.

The request is a future-safe envelope — it carries the structured
``party`` (adults/children/infants); the fan-out derives ``traveler_count``
for today's resolvers. Each response entry is a full ``TrustedActionResult``
plus the ``target`` it belongs to.
"""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .booking_setup import TravelerComposition
from .trusted_action import (
    TrustedActionText,
    TrustedActionResult,
    TrustedActionTripType,
)

BookingOptionsDomain = Literal["transport", "stay"]
BookingModeValue = Literal["flight", "train", "bus"]
BookingPartnerValue = Literal["booking_com", "agoda", "ixigo", "irctc", "redbus", "aviasales"]

_VALID_TARGET_VALUES: dict[str, frozenset[str]] = {
    "mode": frozenset({"flight", "train", "bus"}),
    "partner": frozenset({"booking_com", "agoda", "ixigo"}),
}


class BookingOptionTarget(BaseModel):
    """One thing to resolve: a transport ``mode`` or a stay ``partner``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["mode", "partner"]
    value: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_value_for_kind(self) -> "BookingOptionTarget":
        if self.value not in _VALID_TARGET_VALUES[self.kind]:
            raise ValueError(f"{self.value!r} is not a valid {self.kind} target")
        return self


class BookingOptionsRequest(BaseModel):
    """One open drawer's worth of targets to resolve together.

    ``from_city`` / ``to_city`` (transport) and ``destination`` (stay) are
    optional: an absent field is a deterministic ``missing_input`` outcome on
    every affected target, not a request validation error — mirrors
    ``TrustedActionRequest``'s Optional-fields philosophy.
    """

    model_config = ConfigDict(extra="forbid")

    domain: BookingOptionsDomain
    from_city: Optional[TrustedActionText] = None
    to_city: Optional[TrustedActionText] = None
    destination: Optional[TrustedActionText] = None
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    trip_shape: TrustedActionTripType = "one_way"
    party: TravelerComposition
    targets: list[BookingOptionTarget] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_domain_targets(self) -> "BookingOptionsRequest":
        expected_kind = "mode" if self.domain == "transport" else "partner"
        wrong = [t for t in self.targets if t.kind != expected_kind]
        if wrong:
            raise ValueError(
                f"domain {self.domain!r} takes only {expected_kind!r} targets"
            )
        if self.trip_shape == "one_way" and self.return_date is not None:
            raise ValueError("a one_way request must not include a return_date")
        if (
            self.departure_date is not None
            and self.return_date is not None
            and self.return_date < self.departure_date
        ):
            raise ValueError("return_date cannot be before departure_date")
        return self


class BookingOptionResult(TrustedActionResult):
    """A single ``TrustedActionResult`` tagged with the target it resolves.

    ``provider`` is populated for transport fan-out results so clients can
    render one card per provider while preserving the public mode-target
    request envelope.
    """

    target: BookingOptionTarget
    provider: Optional[BookingPartnerValue] = None


class BookingOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[BookingOptionResult]
