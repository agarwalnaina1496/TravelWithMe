"""Trusted travel-action resolution contract and security policy (TWM-130).

This module defines the provider-neutral shape for a *trusted action* — a
safe way to hand a traveler off to an external surface (a partner search
page, an approved provider) or to point them back at a TWM-owned capability,
without TWM ever fabricating a price, rating, or availability claim it did
not itself resolve.

Only real, this-session-researched partners are modelled — no invented or
unverified provider (e.g. no maps/directions action exists here, since no
maps provider was researched or confirmed).

Three closed action types (TravelActionType):

- CHECK_PRICES: points back at a TWM-owned capability (currently only the
  live flight-search boundary, TWM-144/145) to re-run a price check. Never
  an external URL — see ``internal_capability``.
- PROVIDER: an approved, resolved-price provider handoff (e.g. the
  Aviasales-backed flight offer TWM-145 already produces). This contract
  never lets a PROVIDER action carry the price itself — the price lives on
  ``NormalizedFlightOffer`` (twm/schemas/flight_search.py) or
  ``LogisticsAnchor`` (twm/schemas/logistics.py); this action only carries a
  safe link to *reach* that provider.
- SEARCH_REDIRECT: a prefilled search on a partner site for a mode/domain
  TWM has no live or cached price data of its own for today (train, bus,
  stays, and a flight fallback outside TWM-145/146's Aviasales coverage).
  Carries no price/rating/availability claim — only a safe prefilled query.

Fixed, closed partner allowlist (a Literal, not free text — adding a
partner later is a contract change):

- train: ixigo
- bus: ixigo, redbus
- stay: hotellook, booking_com, agoda, hostelworld
- flight (SEARCH_REDIRECT fallback only): makemytrip

Drive has no action of any kind here — its feasibility is purely computed
(distance/routing), never a partner handoff.

Security posture (structural, not documentation):

- No field on any model here can hold agent- or caller-authored free-text
  URL content. A resolved action's externally-reachable location
  (``ActionTarget.target_url``) is a ``@computed_field`` derived only from
  a fixed per-partner base-domain constant plus a validated path and
  validated query parameters — it is not settable as plain input, and its
  scheme is always ``https`` (enforced by a validator, not convention).
- No query parameter value may itself look like a URL (blocks
  open-redirect-via-param): values containing "://", starting with "//",
  or naming a scheme (http/https/javascript/data/...) are rejected.
- ``affiliate_disclosure: bool`` is mandatory ``True`` whenever the
  resolved partner is one of the affiliate partners above, and mandatory
  ``False`` when there is no partner (CHECK_PRICES) — enforced by a
  validator.
- No model in this file has a price, rating, availability, or
  booking-confirmed field, for any action type. That is
  ``NormalizedFlightOffer``'s territory (a live offer) or
  ``LogisticsAnchor``'s territory (a confirmed booking) — never this
  contract's. This contract is only about reaching a provider safely,
  never about what is found there.
- Evidence and actions are structurally distinct types. ``AtlasReference``
  (twm/schemas/atlas.py) is TWM's evidence/citation shape; nothing in this
  module shares a base class or field shape with it, so a caller cannot
  construct one from the other's data (see tests) and cannot present an
  action as verified evidence or evidence as an actionable link.

Feasibility reasoning (mode/stay comparisons where TWM has no live
provider): flight and drive duration/distance estimates are Backend-computed
deterministically and carry no honesty tag (they are not model output).
Train and bus durations are not derivable deterministically today, so they
are LLM-estimated, bounded, and must carry the same VERIFIED/GENERAL_GUIDANCE
tag Atlas already uses (imported directly from ``twm/schemas/atlas.py`` to
avoid duplicating its honesty-validator logic) — and are structurally
forbidden from ever claiming ``VERIFIED``, since an LLM duration estimate is
never fact-checked.

This is a read/query-style resolution boundary, matching
``twm/services/flight_search/service.py``'s framing: it never mutates trip
lifecycle, plan, selection, or itinerary state.

Contract-only story: TWM-131 implements the resolvers/adapters that
populate this shape (mirroring how TWM-144 defined ``NormalizedFlightOffer``
and TWM-145 later built the Aviasales adapter). No partner-specific HTTP
client and no actual outbound call exist here.

House style: Pydantic v2, extra="forbid" on every model, Literal for closed
enums, model_validator(mode="after") for cross-field invariants — mirrors
twm/schemas/atlas.py and twm/schemas/flight_search.py.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Optional
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator

from .atlas import AtlasReference, VerificationStatus

TrustedActionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

TravelActionType = Literal["CHECK_PRICES", "PROVIDER", "SEARCH_REDIRECT"]
TrustedActionDomain = Literal["flight", "train", "bus", "stay"]
TrustedActionTripType = Literal["one_way", "round_trip"]

# Closed partner allowlist. Adding a partner is a contract change, not a
# runtime config value.
PartnerName = Literal[
    "ixigo",
    "redbus",
    "hotellook",
    "booking_com",
    "agoda",
    "hostelworld",
    "makemytrip",
]

# Every base domain here is a fixed constant, never derived from caller
# input. This is the only place a real hostname is allowed to appear.
_PARTNER_BASE_DOMAIN: dict[PartnerName, str] = {
    "ixigo": "www.ixigo.com",
    "redbus": "www.redbus.in",
    "hotellook": "search.hotellook.com",
    "booking_com": "www.booking.com",
    "agoda": "www.agoda.com",
    "hostelworld": "www.hostelworld.com",
    "makemytrip": "www.makemytrip.com",
}

# Which partners are approved for which domain. PROVIDER/SEARCH_REDIRECT
# partner selection must fall within this map.
_ALLOWED_PARTNERS_BY_DOMAIN: dict[TrustedActionDomain, frozenset[PartnerName]] = {
    "train": frozenset({"ixigo"}),
    "bus": frozenset({"ixigo", "redbus"}),
    "stay": frozenset({"hotellook", "booking_com", "agoda", "hostelworld"}),
    "flight": frozenset({"makemytrip"}),
}

# All partners in the allowlist are affiliate relationships (confirmed this
# session). Kept as an explicit set (not "always True for any partner") so a
# future non-affiliate partner addition cannot silently inherit True.
_AFFILIATE_PARTNERS: frozenset[PartnerName] = frozenset(_PARTNER_BASE_DOMAIN.keys())

_UNSAFE_VALUE_MARKERS = ("://", "//", "javascript:", "data:", "vbscript:")


def _reject_url_like(value: str, *, field_label: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in _UNSAFE_VALUE_MARKERS):
        raise ValueError(f"{field_label} must not contain URL-like content")
    return value


TrustedActionParamValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TrustedActionPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9/_\-\.]+$"),
]


class ActionTarget(BaseModel):
    """The only place an externally-reachable URL is assembled.

    ``target_url`` is a computed field, not a plain input — a caller can
    never set it directly. It is built only from a fixed per-partner base
    domain constant, a validated safe path, and validated query parameters.
    Scheme is always "https", both structurally (never accepted as input)
    and by an explicit validator on the assembled URL (defense in depth).
    """

    model_config = ConfigDict(extra="forbid")

    partner: PartnerName
    path: TrustedActionPath
    query_params: dict[str, TrustedActionParamValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_path_and_params(self) -> "ActionTarget":
        _reject_url_like(self.path, field_label="path")
        for key, value in self.query_params.items():
            _reject_url_like(key, field_label=f"query_params[{key!r}] key")
            _reject_url_like(value, field_label=f"query_params[{key!r}] value")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def target_url(self) -> str:
        base_domain = _PARTNER_BASE_DOMAIN[self.partner]
        path = self.path.lstrip("/")
        query = f"?{urlencode(self.query_params, quote_via=quote)}" if self.query_params else ""
        url = f"https://{base_domain}/{path}{query}"
        if not url.startswith("https://"):
            raise ValueError("target_url must use the https scheme")
        return url


DurationSource = Literal["computed", "llm_estimated"]
TransportMode = Literal["flight", "train", "bus", "drive"]
FeasibilityStatus = Literal["feasible", "ruled_out"]

# flight/drive are Backend-computed deterministically; train/bus have no
# deterministic source today and are LLM-estimated.
_COMPUTED_MODES: frozenset[TransportMode] = frozenset({"flight", "drive"})
_LLM_ESTIMATED_MODES: frozenset[TransportMode] = frozenset({"train", "bus"})

# Sane upper bound for an LLM-estimated overland duration (3 days), to keep
# a hallucinated figure from propagating unbounded.
_MAX_ESTIMATED_DURATION_MINUTES = 4320


class ModeFeasibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: TransportMode
    status: FeasibilityStatus
    duration_source: DurationSource
    estimated_duration_minutes: Optional[int] = Field(default=None, ge=1)
    estimated_distance_km: Optional[float] = Field(default=None, ge=0)
    reason: TrustedActionText
    # Required (and bounded to GENERAL_GUIDANCE) only for llm_estimated
    # modes; always None for computed modes, since a deterministic
    # computation is not model output and has nothing to fact-check.
    verification: Optional[AtlasReference] = None

    @model_validator(mode="after")
    def validate_mode_and_source(self) -> "ModeFeasibility":
        if self.mode in _COMPUTED_MODES and self.duration_source != "computed":
            raise ValueError(f"mode {self.mode} must use duration_source=computed")
        if self.mode in _LLM_ESTIMATED_MODES and self.duration_source != "llm_estimated":
            raise ValueError(f"mode {self.mode} must use duration_source=llm_estimated")
        return self

    @model_validator(mode="after")
    def validate_verification_honesty(self) -> "ModeFeasibility":
        if self.duration_source == "computed":
            if self.verification is not None:
                raise ValueError("computed durations must not carry a verification tag")
        else:  # llm_estimated
            if self.verification is None:
                raise ValueError("llm_estimated durations require a verification tag")
            if self.verification.status == "VERIFIED":
                raise ValueError(
                    "an llm_estimated duration must never claim VERIFIED — it is not fact-checked"
                )
        return self

    @model_validator(mode="after")
    def validate_bounds(self) -> "ModeFeasibility":
        if (
            self.duration_source == "llm_estimated"
            and self.estimated_duration_minutes is not None
            and self.estimated_duration_minutes > _MAX_ESTIMATED_DURATION_MINUTES
        ):
            raise ValueError("llm_estimated duration exceeds the bounded maximum")
        return self


class TripFeasibilityAssessment(BaseModel):
    """Per-mode feasibility reasoning for a route (flight/train/bus/drive).

    Distinct from any action: this never carries a price, only a
    feasibility judgement and a duration/distance estimate with an honest
    provenance tag.
    """

    model_config = ConfigDict(extra="forbid")

    modes: list[ModeFeasibility] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_modes(self) -> "TripFeasibilityAssessment":
        seen = [entry.mode for entry in self.modes]
        if len(seen) != len(set(seen)):
            raise ValueError("each transport mode may appear at most once")
        return self


InternalCapability = Literal["flight_search"]


class TrustedAction(BaseModel):
    """A single resolved, safe travel action.

    Exactly one of ``target`` (an external, allowlisted handoff) or
    ``internal_capability`` (a TWM-owned capability reference) is present,
    determined deterministically by ``action_type`` — never both, never
    neither. There is no price/rating/availability/booking-confirmed field
    anywhere on this model, for any action_type: this contract is only
    about reaching a provider or capability safely, never about what is
    found there.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: TravelActionType
    domain: TrustedActionDomain
    target: Optional[ActionTarget] = None
    internal_capability: Optional[InternalCapability] = None
    affiliate_disclosure: bool

    # Canonical input identity carried onto the resolved action for
    # traceability. Permissive at this stage on purpose — not every field
    # applies to every action_type.
    origin: Optional[TrustedActionText] = None
    destination: Optional[TrustedActionText] = None
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    trip_shape: Optional[TrustedActionTripType] = None
    traveler_count: Optional[int] = Field(default=None, ge=1)

    generated_at: datetime
    expires_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_target_shape(self) -> "TrustedAction":
        if self.action_type == "CHECK_PRICES":
            if self.internal_capability is None:
                raise ValueError("CHECK_PRICES requires internal_capability")
            if self.target is not None:
                raise ValueError("CHECK_PRICES must not carry an external target")
        else:
            if self.target is None:
                raise ValueError(f"{self.action_type} requires target")
            if self.internal_capability is not None:
                raise ValueError(f"{self.action_type} must not carry internal_capability")
        return self

    @model_validator(mode="after")
    def validate_domain_partner(self) -> "TrustedAction":
        if self.action_type in ("PROVIDER", "SEARCH_REDIRECT"):
            partner = self.target.partner if self.target is not None else None
            allowed = _ALLOWED_PARTNERS_BY_DOMAIN.get(self.domain, frozenset())
            if partner not in allowed:
                raise ValueError(f"partner {partner} is not approved for domain {self.domain}")
        return self

    @model_validator(mode="after")
    def validate_affiliate_disclosure(self) -> "TrustedAction":
        partner = self.target.partner if self.target is not None else None
        is_affiliate = partner is not None and partner in _AFFILIATE_PARTNERS
        if is_affiliate and not self.affiliate_disclosure:
            raise ValueError("affiliate_disclosure must be True for an affiliate partner")
        if not is_affiliate and self.affiliate_disclosure:
            raise ValueError("affiliate_disclosure must be False when there is no affiliate partner")
        return self

    @model_validator(mode="after")
    def validate_route_and_dates(self) -> "TrustedAction":
        if self.origin and self.destination and self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must differ")
        if (
            self.departure_date is not None
            and self.return_date is not None
            and self.return_date < self.departure_date
        ):
            raise ValueError("return_date cannot be before departure_date")
        if self.trip_shape == "one_way" and self.return_date is not None:
            raise ValueError("a one_way action must not include a return_date")
        return self


TrustedActionMissingField = Literal[
    "action_type", "domain", "origin", "destination", "departure_date", "return_date", "traveler_count"
]

TrustedActionOutcomeStatus = Literal["resolved", "missing_input", "unsupported_partner", "disabled"]


class TrustedActionRequest(BaseModel):
    """Explicit trusted-action generation input.

    Route, date, and traveler fields are Optional on purpose — a partially
    specified request is not a validation failure, it is a deterministic
    ``missing_input`` outcome (see TrustedActionResult), mirroring
    ``FlightSearchRequest``'s Optional-fields philosophy in
    twm/schemas/flight_search.py.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: TravelActionType
    domain: TrustedActionDomain
    origin: Optional[TrustedActionText] = None
    destination: Optional[TrustedActionText] = None
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    trip_shape: Optional[TrustedActionTripType] = None
    traveler_count: Optional[int] = Field(default=None, ge=1)
    # A caller preference only — final partner selection for a resolved
    # action is still validated against _ALLOWED_PARTNERS_BY_DOMAIN.
    preferred_partner: Optional[PartnerName] = None

    @model_validator(mode="after")
    def validate_route_and_dates(self) -> "TrustedActionRequest":
        if self.origin and self.destination and self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must differ")
        if (
            self.departure_date is not None
            and self.return_date is not None
            and self.return_date < self.departure_date
        ):
            raise ValueError("return_date cannot be before departure_date")
        if self.trip_shape == "one_way" and self.return_date is not None:
            raise ValueError("a one_way request must not include a return_date")
        return self


class TrustedActionMissingInputDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_fields: list[TrustedActionMissingField] = Field(min_length=1)
    message: TrustedActionText


class TrustedActionUnsupportedPartnerDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: TrustedActionDomain
    requested_partner: Optional[PartnerName] = None
    message: TrustedActionText


class TrustedActionDisabledDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: TrustedActionText


class TrustedActionResult(BaseModel):
    """The trip-owned resolution-boundary envelope. ``status`` discriminates
    the outcome so a caller cannot misinterpret e.g. a missing action as
    "no trusted action exists" when it actually means "input still needs
    clarification" or "partner temporarily disabled".

    All four statuses are defined now so the contract is stable for
    recommendation and Atlas itinerary consumers from day one, even though
    TWM-131 (not yet started) is what will ever actually populate
    ``resolved`` — mirrors FlightSearchResponse's status discriminator in
    twm/schemas/flight_search.py.
    """

    model_config = ConfigDict(extra="forbid")

    status: TrustedActionOutcomeStatus
    generated_at: datetime
    action: Optional[TrustedAction] = None
    missing_input: Optional[TrustedActionMissingInputDetail] = None
    unsupported_partner: Optional[TrustedActionUnsupportedPartnerDetail] = None
    disabled: Optional[TrustedActionDisabledDetail] = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> "TrustedActionResult":
        if self.status == "resolved":
            if self.action is None:
                raise ValueError("action is required when status is resolved")
            if self.missing_input or self.unsupported_partner or self.disabled:
                raise ValueError("resolved must not carry missing_input/unsupported_partner/disabled")
        elif self.status == "missing_input":
            if self.missing_input is None:
                raise ValueError("missing_input is required when status is missing_input")
            if self.action or self.unsupported_partner or self.disabled:
                raise ValueError("missing_input must not carry action/unsupported_partner/disabled")
        elif self.status == "unsupported_partner":
            if self.unsupported_partner is None:
                raise ValueError("unsupported_partner is required when status is unsupported_partner")
            if self.action or self.missing_input or self.disabled:
                raise ValueError("unsupported_partner must not carry action/missing_input/disabled")
        elif self.status == "disabled":
            if self.disabled is None:
                raise ValueError("disabled is required when status is disabled")
            if self.action or self.missing_input or self.unsupported_partner:
                raise ValueError("disabled must not carry action/missing_input/unsupported_partner")
        return self


# --- Consumer reference shape -------------------------------------------------
#
# How a recommendation or an Atlas itinerary item references a trusted
# action: it embeds a `TrustedActionResult` (typically via a
# `trusted_action: Optional[TrustedActionResult]` field added to the
# consuming schema once TWM-131/TWM-132 wire an actual resolver and UI).
# Re-exporting `VerificationStatus` here only so a future consumer schema
# can express "this trusted_action's feasibility carries GENERAL_GUIDANCE
# durations" without a second import path.
__all__ = [
    "TravelActionType",
    "TrustedActionDomain",
    "TrustedActionTripType",
    "PartnerName",
    "ActionTarget",
    "TransportMode",
    "FeasibilityStatus",
    "DurationSource",
    "ModeFeasibility",
    "TripFeasibilityAssessment",
    "InternalCapability",
    "TrustedAction",
    "TrustedActionMissingField",
    "TrustedActionOutcomeStatus",
    "TrustedActionRequest",
    "TrustedActionMissingInputDetail",
    "TrustedActionUnsupportedPartnerDetail",
    "TrustedActionDisabledDetail",
    "TrustedActionResult",
    "VerificationStatus",
]
