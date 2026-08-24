"""Trusted-action application service (TWM-131).

Turns a ``TrustedActionRequest`` (twm/schemas/trusted_action.py, TWM-130)
into a status-discriminated ``TrustedActionResult`` — missing_input,
unsupported_partner, or resolved. Pure/deterministic: no outbound HTTP
call, no persistence write, no trip-state mutation (mirrors
twm/services/flight_search/service.py's read/query-boundary framing). The
``disabled`` status is a valid TWM-130 contract value but is intentionally
unreachable here — no operational kill-switch was requested this story
(see module docstring in twm/schemas/trusted_action.py).

Feasibility assessment (``TripFeasibilityAssessment``) is a related but
structurally separate concern — it has no field on ``TrustedActionResult``
to attach to (TWM-131 must not add one without touching TWM-130's
contract), and it answers a different question (which transport modes are
plausible for a route at all) than trusted-action resolution (how to reach
a specific partner/capability). ``assess_feasibility`` is exposed as its
own service method / router endpoint rather than folded in.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from ...schemas.trusted_action import (
    TrustedAction,
    TrustedActionMissingInputDetail,
    TrustedActionRequest,
    TrustedActionResult,
    TrustedActionUnsupportedPartnerDetail,
    TripFeasibilityAssessment,
)
from ...telemetry import TelemetryLogger
from .calculations import missing_required_fields, resolve_partner
from .feasibility import assess_trip_feasibility
from .resolvers import resolve_partner_target
from .settings import TrustedActionSettings

_MISSING_INPUT_MESSAGE = "Tell us the missing trip details so we can build this action."
_UNSUPPORTED_PARTNER_MESSAGE = "No approved partner is available for this request."


@dataclass
class TrustedActionService:
    logger: TelemetryLogger
    settings: TrustedActionSettings

    def resolve(self, trip_id: UUID, request: TrustedActionRequest) -> TrustedActionResult:
        generated_at = datetime.now(timezone.utc)
        request_data = request.model_dump(mode="json", exclude_none=True)
        self.logger.info(
            "Received trusted-action resolution request.",
            event="be.trusted_action.requested",
            source="application",
            trip_id=str(trip_id),
            payload=request_data,
        )

        missing = missing_required_fields(request)
        if missing:
            self.logger.info(
                "Rejected trusted-action request pending traveler clarification.",
                event="be.trusted_action.readiness_rejected",
                source="application",
                trip_id=str(trip_id),
                missing_fields=missing,
            )
            return TrustedActionResult(
                status="missing_input",
                generated_at=generated_at,
                missing_input=TrustedActionMissingInputDetail(
                    missing_fields=missing, message=_MISSING_INPUT_MESSAGE
                ),
            )

        if request.action_type == "CHECK_PRICES":
            action = TrustedAction(
                action_type="CHECK_PRICES",
                domain=request.domain,
                internal_capability="flight_search",
                affiliate_disclosure=False,
                origin=request.origin,
                destination=request.destination,
                departure_date=request.departure_date,
                return_date=request.return_date,
                trip_shape=request.trip_shape,
                traveler_count=request.traveler_count,
                generated_at=generated_at,
            )
            self._log_resolved(trip_id, action)
            return TrustedActionResult(status="resolved", generated_at=generated_at, action=action)

        partner = resolve_partner(request)
        if partner is None:
            self.logger.info(
                "No approved partner for trusted-action request.",
                event="be.trusted_action.partner_unsupported",
                source="application",
                trip_id=str(trip_id),
                domain=request.domain,
                requested_partner=request.preferred_partner,
            )
            return TrustedActionResult(
                status="unsupported_partner",
                generated_at=generated_at,
                unsupported_partner=TrustedActionUnsupportedPartnerDetail(
                    domain=request.domain,
                    requested_partner=request.preferred_partner,
                    message=_UNSUPPORTED_PARTNER_MESSAGE,
                ),
            )

        target = resolve_partner_target(request, partner=partner, settings=self.settings)
        action = TrustedAction(
            action_type=request.action_type,
            domain=request.domain,
            target=target,
            affiliate_disclosure=True,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            trip_shape=request.trip_shape,
            traveler_count=request.traveler_count,
            generated_at=generated_at,
        )
        self._log_resolved(trip_id, action)
        return TrustedActionResult(status="resolved", generated_at=generated_at, action=action)

    def assess_feasibility(
        self, trip_id: UUID, origin: str, destination: str
    ) -> TripFeasibilityAssessment:
        """Deterministic, synchronous route-mode feasibility assessment
        (TWM-195 root fix -- no classifier/LLM/agent call of any kind).
        Always returns a real ``TripFeasibilityAssessment``; ``modes`` is
        empty when the route could not be confidently assessed."""

        self.logger.info(
            "Received trip-feasibility assessment request.",
            event="be.trusted_action.feasibility.requested",
            source="application",
            trip_id=str(trip_id),
            segment_count=1,
        )
        assessment = assess_trip_feasibility(origin, destination)
        returned_modes = [entry.mode for entry in assessment.modes]
        if returned_modes:
            self.logger.info(
                "Resolved trip-feasibility assessment.",
                event="be.trusted_action.feasibility.resolved",
                source="application",
                trip_id=str(trip_id),
                returned_mode_count=len(returned_modes),
                returned_modes=returned_modes,
            )
        else:
            self.logger.warning(
                "Trip-feasibility assessment resolved with no route-valid "
                "modes (cannot-assess or genuinely no bookable modes).",
                event="be.trusted_action.feasibility.empty",
                source="application",
                trip_id=str(trip_id),
            )
        return assessment

    def _log_resolved(self, trip_id: UUID, action: TrustedAction) -> None:
        self.logger.info(
            "Resolved trusted action.",
            event="be.trusted_action.resolved",
            source="application",
            trip_id=str(trip_id),
            action_type=action.action_type,
            domain=action.domain,
            partner=action.target.partner if action.target is not None else None,
            internal_capability=action.internal_capability,
        )
