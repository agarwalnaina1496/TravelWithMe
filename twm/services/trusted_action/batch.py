"""Batch booking-options fan-out (TWM-220).

A pure server-side fan-out over :meth:`TrustedActionService.resolve` — one
open drawer's worth of targets resolved in a single request. No new business
logic: every target is turned into the same ``TrustedActionRequest`` the
single-action endpoint would receive, resolved through the identical path,
and collected. One target's non-``resolved`` outcome never fails the batch.

The structured ``party`` is passed straight through to each request (as both
``traveler_party`` and, for callers that still read a single total,
``traveler_count``) so a provider deep link can fill adults / children /
infants separately.
"""

from uuid import UUID

from ...schemas.booking_options import (
    BookingOptionResult,
    BookingOptionTarget,
    BookingOptionsRequest,
    BookingOptionsResponse,
)
from ...schemas.trusted_action import TrustedActionRequest
from .service import TrustedActionService


def _request_for_target(
    payload: BookingOptionsRequest, target: BookingOptionTarget
) -> TrustedActionRequest:
    common = {
        "action_type": "SEARCH_REDIRECT",
        "departure_date": payload.departure_date,
        "return_date": payload.return_date,
        "trip_shape": payload.trip_shape,
        "traveler_count": payload.party.total,
        "traveler_party": payload.party,
    }
    if target.kind == "mode":
        return TrustedActionRequest(
            domain=target.value,
            origin=payload.from_city,
            destination=payload.to_city,
            **common,
        )
    return TrustedActionRequest(
        domain="stay",
        destination=payload.destination,
        preferred_partner=target.value,
        **common,
    )


def resolve_booking_options(
    service: TrustedActionService, trip_id: UUID, payload: BookingOptionsRequest
) -> BookingOptionsResponse:
    results: list[BookingOptionResult] = []
    for target in payload.targets:
        outcome = service.resolve(trip_id, _request_for_target(payload, target))
        # Pass the already-validated nested model instances straight through
        # rather than round-tripping a dump — re-validating a dumped
        # ``ActionTarget`` would trip its ``extra="forbid"`` on the computed
        # ``target_url`` key.
        results.append(
            BookingOptionResult(
                target=target,
                status=outcome.status,
                generated_at=outcome.generated_at,
                action=outcome.action,
                missing_input=outcome.missing_input,
                unsupported_partner=outcome.unsupported_partner,
                disabled=outcome.disabled,
            )
        )

    resolved_count = sum(1 for r in results if r.status == "resolved")
    service.logger.info(
        "Resolved a batch of booking options.",
        event="be.trip.booking_options.batch",
        source="application",
        trip_id=str(trip_id),
        domain=payload.domain,
        target_count=len(payload.targets),
        resolved_count=resolved_count,
    )
    return BookingOptionsResponse(results=results)
