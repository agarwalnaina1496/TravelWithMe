"""Deterministic readiness/partner-selection helpers for trusted actions
(TWM-131). Mirrors twm/services/flight_search/calculations.py's
``missing_required_fields`` template.
"""

from typing import Optional

from ...schemas.trusted_action import (
    PartnerName,
    TrustedActionDomain,
    TrustedActionMissingField,
    TrustedActionRequest,
)

# Service-level convenience copy of twm/schemas/trusted_action.py's
# documented partner allowlist (module docstring / private
# ``_ALLOWED_PARTNERS_BY_DOMAIN``), used here only for a cheap
# unsupported-partner pre-check and default-partner selection.
# ``TrustedAction``'s own validators in that module remain the enforced
# source of truth (defense in depth) — if this copy ever drifted stale, the
# service could only become *more* restrictive than the schema, never
# silently permit something the schema would reject.
_ALLOWED_PARTNERS_BY_DOMAIN: dict[TrustedActionDomain, tuple[PartnerName, ...]] = {
    "flight": ("ixigo",),
    "train": ("ixigo",),
    "bus": ("ixigo", "redbus"),
    "stay": ("hotellook", "booking_com", "agoda", "hostelworld", "ixigo"),
}


def missing_required_fields(request: TrustedActionRequest) -> list[TrustedActionMissingField]:
    """The deterministic set of missing inputs for resolving a trusted
    action, if any. A partially specified request is not a validation
    failure — it is a typed ``missing_input`` outcome (see
    TrustedActionResult)."""

    missing: list[TrustedActionMissingField] = []
    if request.origin is None:
        missing.append("origin")
    if request.destination is None:
        missing.append("destination")
    if request.departure_date is None:
        missing.append("departure_date")
    if request.trip_shape == "round_trip" and request.return_date is None:
        missing.append("return_date")
    if request.traveler_count is None:
        missing.append("traveler_count")
    return missing


def allowed_partners(domain: TrustedActionDomain) -> tuple[PartnerName, ...]:
    return _ALLOWED_PARTNERS_BY_DOMAIN.get(domain, ())


def resolve_partner(request: TrustedActionRequest) -> Optional[PartnerName]:
    """The partner a resolved PROVIDER/SEARCH_REDIRECT action should use, or
    None when no approved partner exists for this request (the caller must
    then produce an ``unsupported_partner`` outcome).

    A caller-supplied ``preferred_partner`` is honoured only when it is
    approved for this domain; an unapproved preference is never silently
    substituted with a different partner — the caller sees
    ``unsupported_partner`` instead, naming what was actually requested.
    """

    allowed = allowed_partners(request.domain)
    if request.preferred_partner is not None:
        return request.preferred_partner if request.preferred_partner in allowed else None
    return allowed[0] if allowed else None
