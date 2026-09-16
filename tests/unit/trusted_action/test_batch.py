"""Unit tests for the booking-options batch fan-out (TWM-220).

A stubbed ``TrustedActionService.resolve`` -- these exercise batch.py's own
collection/pass-through logic (one non-resolved target never fails the
batch) independent of which real partner happens to have a narrower
capability than its domain-mates. Previously this was only exercised via
Agoda's Goa-only capability at the API level (apitest_booking_options.py);
dropping Agoda (TWM-230 Increment 2b) removed the only real partner with a
capability narrower than its domain, so this unit test fills that gap
directly rather than losing the coverage.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from twm.schemas.booking_options import BookingOptionsRequest, BookingOptionTarget
from twm.schemas.booking_setup import TravelerComposition
from twm.schemas.trusted_action import (
    TrustedActionDisabledDetail,
    TrustedActionMissingInputDetail,
    TrustedActionResult,
)
from twm.services.trusted_action.batch import resolve_booking_options

_NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
_PARTY = TravelerComposition(adults=2, children=0, infants=0)


class _StubLogger:
    def info(self, *args, **kwargs) -> None:
        pass


def _stub_service(outcomes_by_partner: dict[str, TrustedActionResult]):
    def resolve(trip_id, request):
        return outcomes_by_partner[request.preferred_partner]

    return SimpleNamespace(resolve=resolve, logger=_StubLogger())


def test_one_target_disabled_does_not_fail_the_batch():
    outcomes = {
        "booking_com": TrustedActionResult(
            status="missing_input",
            generated_at=_NOW,
            missing_input=TrustedActionMissingInputDetail(missing_fields=["destination"], message="Tell us more."),
        ),
        "ixigo": TrustedActionResult(
            status="disabled",
            generated_at=_NOW,
            disabled=TrustedActionDisabledDetail(reason="No confirmed useful provider redirect is available."),
        ),
    }

    payload = BookingOptionsRequest(
        domain="stay",
        destination="Coorg",
        party=_PARTY,
        targets=[
            BookingOptionTarget(kind="partner", value="booking_com"),
            BookingOptionTarget(kind="partner", value="ixigo"),
        ],
    )

    response = resolve_booking_options(_stub_service(outcomes), uuid4(), payload)

    by_partner = {r.target.value: r.status for r in response.results}
    assert by_partner == {"booking_com": "missing_input", "ixigo": "disabled"}
