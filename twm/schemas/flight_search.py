"""Provider-neutral live flight-search and offer contract (TWM-144).

This module defines the typed request/readiness/offer contract for an
explicit live flight search. It is deliberately shaped against the
currently-reachable Aviasales provider generation — the "Data API"
(cached cheapest-price lookups, no MAU gate) — not the real-time
"Flight Search API" (search_id-based live search), which requires a
minimum monthly-active-users threshold TWM does not yet meet.

Consequences of that provider reality, encoded directly in these schemas:

- There is no live per-request search transaction (no search_id/session).
  Every offer is a single cached price point the provider found earlier,
  not something TWM queried live in this call.
- The provider discloses no traveler-count filter, no rich cabin-class
  filter, and no baggage/fare-condition data. Fields that model these
  concepts exist only for provider-neutrality (so a future richer provider
  can populate them) and are always None for the current provider
  generation — they are never invented or guessed.
- A "group total" is therefore always a Backend-computed
  per_traveler_amount * traveler_count approximation, never a
  provider-confirmed multi-passenger fare. FlightMoney.group_total_is_approximate
  makes this explicit as a typed field, not a comment travelers/callers
  could miss.
- The provider discloses a departure timestamp and an IATA carrier code
  (airline_code, flight_number) but no arrival time and no full airline
  name — airline_name is populated only from a static code->name lookup
  (twm/services/flight_search/airlines.py), never guessed, and there is
  deliberately no arrival_at field since no current provider input can
  supply one honestly. The offer carries one point price
  (FlightMoney.per_traveler_amount_minor_units), not a low/high range —
  a UI showing this data should render a single value with a freshness
  badge, not fabricate a range.

TWM-145 selects and wires the Aviasales adapter against this contract
(twm/services/flight_search/aviasales.py).

House style: Pydantic v2, extra="forbid" on every model, Literal for closed
enums, model_validator(mode="after") for cross-field invariants — mirrors
twm/schemas/atlas.py and twm/schemas/logistics.py.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


FlightText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
IataCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"
    ),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"
    ),
]

FlightTripType = Literal["one_way", "round_trip"]

# The result-level discriminator. "offer"/"partial" are only reachable once
# TWM-145 wires a provider; "expired" requires a cache TWM-144 does not wire
# (see FlightSearchCacheEntry). All six are defined now so the contract is
# stable and callers can exhaustively switch on it from day one.
FlightSearchResponseStatus = Literal[
    "clarification_needed", "unavailable", "offer", "expired", "partial", "failed"
]

FlightSearchMissingField = Literal[
    "origin", "destination", "departure_date", "return_date", "travelers"
]


class FlightSearchKeys:
    """Flight Search request payload field names — the source of truth
    for code that builds or checks these fields by name (e.g.
    ``missing_required_fields``) instead of re-hardcoding the literals
    already enumerated in ``FlightSearchMissingField`` above."""

    ORIGIN = "origin"
    DESTINATION = "destination"
    DEPARTURE_DATE = "departure_date"
    RETURN_DATE = "return_date"
    TRAVELERS = "travelers"

FlightSearchUnavailableCode = Literal[
    "provider_not_configured",
    "provider_unauthorized",
    "provider_timeout",
    "provider_rejected_request",
]

FlightSearchFailureCode = Literal["invalid_request", "internal_error"]


class FlightSearchTravelerCount(BaseModel):
    """Used only to compute an approximate group total locally — the
    current provider generation has no per-traveler search filter, so this
    is never forwarded to a provider as a search constraint."""

    model_config = ConfigDict(extra="forbid")

    adults: int = Field(ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def validate_bounds(self) -> "FlightSearchTravelerCount":
        if self.adults + self.children + self.infants > 9:
            raise ValueError("total travelers must not exceed 9")
        if self.infants > self.adults:
            raise ValueError("infants cannot exceed adults")
        return self


class FlightSearchBudgetCeiling(BaseModel):
    """A hard ceiling applied to the Backend-computed group total. Never
    forwarded to a provider as a search filter — no reachable provider
    supports server-side budget filtering."""

    model_config = ConfigDict(extra="forbid")

    currency: CurrencyCode
    group_total_minor_units_max: int = Field(ge=0)


class FlightSearchRequest(BaseModel):
    """Explicit live flight-search input.

    Route, date, and traveler fields are Optional at the schema level on
    purpose: a vague or partially-specified request (e.g. "sometime in
    September", an unresolved destination) is not a validation failure —
    it is a deterministic clarification outcome, evaluated by
    FlightSearchService. Only malformed values (wrong type, extra fields,
    out-of-range counts, an inconsistent date/trip_type combination) are
    rejected with 422 by this schema.

    cabin/class deliberately does not appear here: the current provider
    generation (Data API) exposes only a coarse trip_class on unrelated
    calendar-matrix endpoints, not a real cabin-fare concept, and no
    reachable endpoint filters a cheapest-price lookup by cabin.
    """

    model_config = ConfigDict(extra="forbid")

    origin_iata: Optional[IataCode] = None
    destination_iata: Optional[IataCode] = None
    trip_type: FlightTripType = "round_trip"
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    travelers: Optional[FlightSearchTravelerCount] = None
    max_stops: Optional[int] = Field(default=None, ge=0, le=3)
    budget_ceiling: Optional[FlightSearchBudgetCeiling] = None

    @model_validator(mode="after")
    def validate_route_and_dates(self) -> "FlightSearchRequest":
        if (
            self.origin_iata
            and self.destination_iata
            and self.origin_iata == self.destination_iata
        ):
            raise ValueError("origin and destination must differ")
        if (
            self.departure_date is not None
            and self.return_date is not None
            and self.return_date < self.departure_date
        ):
            raise ValueError("return_date cannot be before departure_date")
        if self.trip_type == "one_way" and self.return_date is not None:
            raise ValueError("a one_way search must not include a return_date")
        return self


class FlightSearchClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_fields: list[FlightSearchMissingField] = Field(min_length=1)
    message: FlightText


class FlightSearchUnavailableReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: FlightSearchUnavailableCode
    message: FlightText


class FlightSearchFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: FlightSearchFailureCode
    message: FlightText


class FlightProviderProvenance(BaseModel):
    """Inert provenance only. No raw provider payload field exists on this
    model (not even an escape-hatch Optional[dict]), and there is no url
    field — a live offer never doubles as a booking-authority action
    object (that concept is TWM-129's, out of scope here)."""

    model_config = ConfigDict(extra="forbid")

    provider_name: FlightText
    provider_reference: FlightText  # opaque id/lookup key — never a URL


class FlightMoney(BaseModel):
    """Normalized pricing for one offer.

    The current provider generation (Aviasales Data API) returns a
    single cached found price, not a confirmed multi-passenger fare
    breakdown. group_total_minor_units is therefore always Backend-computed
    as per_traveler_amount_minor_units * traveler_count —
    group_total_is_approximate is always True while this remains the only
    reachable provider generation, and is a modeled (not commented) field
    so a caller can never mistake it for a provider-confirmed group fare.
    """

    model_config = ConfigDict(extra="forbid")

    currency: CurrencyCode
    per_traveler_amount_minor_units: int = Field(ge=0)
    traveler_count: int = Field(ge=1)
    group_total_minor_units: int = Field(ge=0)
    group_total_is_approximate: bool
    # The provider does not disclose whether its cached price includes
    # taxes/fees, so this is always None for the current provider
    # generation rather than a guessed True/False.
    tax_fee_included: Optional[bool] = None

    @model_validator(mode="after")
    def validate_group_total(self) -> "FlightMoney":
        expected = self.per_traveler_amount_minor_units * self.traveler_count
        if self.group_total_minor_units != expected:
            raise ValueError(
                "group_total_minor_units must equal "
                "per_traveler_amount_minor_units * traveler_count"
            )
        return self


class FlightBaggageAllowance(BaseModel):
    """Reserved for provider-neutrality. The current provider generation
    discloses no baggage information, so both fields are always None until
    a richer provider is integrated."""

    model_config = ConfigDict(extra="forbid")

    checked_bag_included: Optional[bool] = None
    cabin_bag_included: Optional[bool] = None


class FlightFareConditions(BaseModel):
    """Reserved for provider-neutrality; unpopulated by the current
    provider generation."""

    model_config = ConfigDict(extra="forbid")

    refundable: Optional[bool] = None
    changeable: Optional[bool] = None


class NormalizedFlightOffer(BaseModel):
    """A single cached price point normalized to a provider-neutral shape.

    Deliberately distinct from every other TWM cost/evidence/action
    concept: it is not an Atlas qualified estimate
    (AtlasTimelineItem.estimated_cost_low/high — a different type with no
    shared base class implying equivalence), not evidence (AtlasReference's
    VERIFIED/GENERAL_GUIDANCE), not a trusted action/booking-link object
    (no url field, no booking authority), and not a confirmed booking
    (LogisticsAnchor — a booked fact; a live offer is never in that
    state).

    price_found_at names the provider's own cache timestamp honestly (when
    the provider's cache found this price) — distinct from the result
    envelope's queried_at (when TWM issued this search).
    """

    model_config = ConfigDict(extra="forbid")

    origin_iata: IataCode
    destination_iata: IataCode
    trip_type: FlightTripType
    departure_date: date
    departure_at: Optional[datetime] = None
    return_date: Optional[date] = None
    # Only populated when the provider endpoint discloses it (e.g. a
    # calendar/matrix lookup's transfers count); None otherwise — never
    # fabricated.
    stop_count: Optional[int] = Field(default=None, ge=0)
    # IATA carrier code as disclosed by the provider (e.g. "6E"). The
    # provider does not disclose a full airline name or an arrival time —
    # airline_name is populated only from TWM's own static code->name
    # lookup (see normalization.py), never guessed; arrival time has no
    # field here at all because no current provider input can supply it
    # honestly.
    airline_code: Optional[FlightText] = None
    airline_name: Optional[FlightText] = None
    flight_number: Optional[FlightText] = None
    money: FlightMoney
    baggage: FlightBaggageAllowance
    fare_conditions: FlightFareConditions
    provenance: FlightProviderProvenance
    price_found_at: datetime
    offer_expires_at: Optional[datetime] = None
    # Backend-computed only (see rank_offers/service.py). Never provider-
    # sourced and never caller-settable as a way to spoof TWM's pick — this
    # field exists on the response shape, not on FlightSearchRequest, so no
    # caller input can ever set it; the service always constructs offers
    # itself via model_copy(update=...) after ranking, the same mechanism
    # already used for stop_count enrichment.
    is_recommended: bool = False

    @model_validator(mode="after")
    def validate_return_date_matches_trip_type(self) -> "NormalizedFlightOffer":
        if self.trip_type == "round_trip" and self.return_date is None:
            raise ValueError("round_trip offers require a return_date")
        if self.trip_type == "one_way" and self.return_date is not None:
            raise ValueError("one_way offers must not include a return_date")
        return self


class FlightExplanationCandidate(BaseModel):
    """Bounded payload for an optional later LLM explanation step.

    No credentials, no raw provider payload, and no URL — an LLM must
    never be able to author a booking URL from this shape.
    """

    model_config = ConfigDict(extra="forbid")

    provider_name: FlightText
    origin_iata: IataCode
    destination_iata: IataCode
    stop_count: Optional[int] = Field(default=None, ge=0)
    currency: CurrencyCode
    group_total_minor_units: int = Field(ge=0)
    group_total_is_approximate: bool
    departure_window: FlightText


class FlightSearchResponse(BaseModel):
    """The trip-owned API response envelope. status discriminates the
    outcome so callers cannot misinterpret e.g. an empty offers list as
    "no flights found" when it actually means "provider not configured yet"
    or "traveler input still needs clarification"."""

    model_config = ConfigDict(extra="forbid")

    status: FlightSearchResponseStatus
    queried_at: datetime
    expires_at: Optional[datetime] = None
    clarification: Optional[FlightSearchClarification] = None
    offers: list[NormalizedFlightOffer] = Field(default_factory=list)
    unavailable: Optional[FlightSearchUnavailableReason] = None
    failure: Optional[FlightSearchFailure] = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> "FlightSearchResponse":
        if self.status == "clarification_needed":
            if self.clarification is None:
                raise ValueError(
                    "clarification is required when status is clarification_needed"
                )
            if self.offers or self.unavailable or self.failure:
                raise ValueError(
                    "clarification_needed must not carry offers/unavailable/failure"
                )
        elif self.status == "unavailable":
            if self.unavailable is None:
                raise ValueError("unavailable is required when status is unavailable")
            if self.offers or self.clarification or self.failure:
                raise ValueError(
                    "unavailable must not carry clarification/offers/failure"
                )
        elif self.status == "failed":
            if self.failure is None:
                raise ValueError("failure is required when status is failed")
            if self.offers or self.clarification or self.unavailable:
                raise ValueError(
                    "failed must not carry clarification/offers/unavailable"
                )
        elif self.status in ("offer", "partial"):
            if not self.offers:
                raise ValueError(f"status {self.status} requires at least one offer")
            if self.clarification or self.unavailable or self.failure:
                raise ValueError(
                    f"{self.status} must not carry clarification/unavailable/failure"
                )
        elif self.status == "expired":
            if self.clarification or self.unavailable or self.failure:
                raise ValueError(
                    "expired must not carry clarification/unavailable/failure"
                )
        return self


class FlightSearchCacheKey(BaseModel):
    """Sanitized, persistable search-query shape — safe to cache/store."""

    model_config = ConfigDict(extra="forbid")

    origin_iata: IataCode
    destination_iata: IataCode
    trip_type: FlightTripType
    departure_date: date
    return_date: Optional[date] = None
    traveler_count: int = Field(ge=1)
    max_stops: Optional[int] = Field(default=None, ge=0, le=3)


class FlightSearchCacheEntry(BaseModel):
    """Safe persistence/cache boundary shape.

    A sanitized query plus a bounded normalized shortlist. Raw provider
    responses, raw request payloads, and credentials are never persisted —
    there is deliberately no such field on this model, not even an escape
    hatch typed as Optional[dict]. Defined now as a shape only; TWM-144
    never produces a cacheable offer (no provider is integrated), so this
    is not wired to a live store yet. TWM-145 wires the actual cache once a
    provider is selected.
    """

    model_config = ConfigDict(extra="forbid")

    query: FlightSearchCacheKey
    offers: list[NormalizedFlightOffer] = Field(default_factory=list, max_length=10)
    provider_name: FlightText
    queried_at: datetime
    expires_at: datetime
