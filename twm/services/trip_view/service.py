"""``TripViewService`` — the single composer for the ``TripView`` read model
(TWM-217).

A Builder, not a monolith: ``build()`` only orchestrates; every block is its
own pure, independently-testable ``_compose_*`` method. Adding a ``TripView``
field means one new ``_compose_*`` and one line in ``build()`` — never a
value wired straight from a router or a repository read. The fitness-function
test enforces that.

Reads: the composed in-memory ``trip_state`` (lifecycle columns + branch
tables, already merged by the repository), the stored
``itinerary_versions.result`` when an itinerary exists, and a
``has_recommendation`` boolean. Never the prose — ``timeline[].detail`` /
``movement_guidance`` / per-day note text are only ever on ``GET /itinerary``.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from uuid import UUID

from ...schemas.trip_context import DESTINATIONS_KEY, FIXED_KEYS
from ...schemas.trip_view import (
    BeforeYouGoItem,
    BudgetBreakdown,
    BudgetLine,
    ContextRecapItem,
    OpenGap,
    TravelWindow,
    TripListItem,
    SummaryBudget,
    SummaryDates,
    SummaryTravelers,
    TravelerParty,
    TripView,
    TripViewBooking,
    TripViewLifecycle,
    TripViewMatcher,
    TripViewPlan,
    TripViewPlanDay,
    TripViewSummary,
)
from .trip_dates import compose_trip_dates, recap_label

_TRAVEL_DATES_KEY = "travel_dates"
_RECAP_LABELS = {
    "origin_city": "Coming from",
    "num_travelers": "Travellers",
    "trip_duration": "Trip length",
    _TRAVEL_DATES_KEY: "When",
    "budget": "Budget",
    DESTINATIONS_KEY: "Destination",
}
_RECAP_KEYS = (*FIXED_KEYS, DESTINATIONS_KEY)

_ASSUMPTION_TITLES = {
    "stay_area": "Where you'll stay",
    "budget": "Your budget",
    "arrival_departure_window": "Arrival & departure timing",
}


class TripViewService:
    """Constructed once at startup (pure — no external boundary)."""

    def build(
        self,
        *,
        trip_id: UUID,
        title: str,
        product_mode: str,
        version: int,
        trip_state: dict[str, Any],
        ui_state: dict[str, Any],
        itinerary_result: Optional[dict[str, Any]],
        has_recommendation: bool,
    ) -> TripView:
        final_itinerary = (itinerary_result or {}).get("final_itinerary")
        trip_context = trip_state.get("trip_context") or {}
        planner_state = trip_state.get("planner_state") or {}
        matcher_state = trip_state.get("matcher_state") or {}
        booking_setup = trip_state.get("booking_setup") or {}

        booking = self._compose_booking(booking_setup)
        summary = self._compose_summary(final_itinerary, trip_context, booking)

        return TripView(
            id=trip_id,
            title=title,
            product_mode=product_mode,
            version=version,
            ui_state=ui_state,
            lifecycle=self._compose_lifecycle(trip_state),
            context_recap=self._compose_context_recap(trip_context),
            plan=self._compose_plan(planner_state),
            matcher=self._compose_matcher(matcher_state, has_recommendation),
            summary=summary,
            booking=None if final_itinerary is None else booking,
            budget_breakdown=self._compose_budget_breakdown(final_itinerary, trip_context, booking, summary),
            open_gaps=self._compose_open_gaps(final_itinerary, booking),
            before_you_go=self._compose_before_you_go(final_itinerary),
        )

    def build_list_item(
        self,
        *,
        trip_id: UUID,
        title: str,
        product_mode: str,
        version: int,
        created_at: Any,
        updated_at: Any,
        trip_state: dict[str, Any],
        has_recommendation: bool,
    ) -> TripListItem:
        """`GET /trips` — the thin subset, still composer-owned so the
        derivation stays in one place."""
        planner_state = trip_state.get("planner_state") or {}
        return TripListItem(
            id=trip_id,
            title=title,
            product_mode=product_mode,
            version=version,
            created_at=created_at,
            updated_at=updated_at,
            lifecycle=self._compose_lifecycle(trip_state),
            context_recap=self._compose_context_recap(trip_state.get("trip_context") or {}),
            travel_window=self._compose_travel_window(trip_state.get("trip_context") or {}),
            has_places=bool(planner_state.get("places")),
            has_day_plan=bool(planner_state.get("day_plan")),
            has_itinerary=(trip_state.get("itinerary_state") or {}).get("status") == "ready",
            awaiting=(planner_state.get("conversation_context") or {}).get("awaiting"),
            has_recommendation=has_recommendation,
        )

    # ---- resume state (always present) -----------------------------------

    def _compose_lifecycle(self, trip_state: dict[str, Any]) -> TripViewLifecycle:
        return TripViewLifecycle(
            stage=trip_state.get("stage", "new"),
            status=trip_state.get("status", "free"),
            active_agent=trip_state.get("active_agent"),
            selected_option=trip_state.get("selected_option"),
        )

    def _compose_context_recap(self, trip_context: dict[str, Any]) -> list[ContextRecapItem]:
        items: list[ContextRecapItem] = []
        for key in _RECAP_KEYS:
            if key not in trip_context or trip_context[key] in (None, "", []):
                continue
            # The "When" row shows a composed label, so it stays readable
            # whether `travel_dates` was stored as ISO or the traveler's prose.
            value = (
                (key == _TRAVEL_DATES_KEY and recap_label(trip_context))
                or _coerce_display(trip_context[key])
            )
            items.append(ContextRecapItem(key=key, label=_RECAP_LABELS[key], value=value))
        return items

    def _compose_plan(self, planner_state: dict[str, Any]) -> Optional[TripViewPlan]:
        awaiting = (planner_state.get("conversation_context") or {}).get("awaiting")
        places = list(planner_state.get("places") or [])
        raw_day_plan = planner_state.get("day_plan") or []
        if not (places or raw_day_plan or awaiting or planner_state.get("frozen_plan")):
            return None
        return TripViewPlan(
            places=places,
            day_plan=[
                TripViewPlanDay(
                    day_number=day["day_number"],
                    places=list(day.get("places") or []),
                    pace=day.get("pace"),
                    buffer_note=day.get("buffer_note"),
                )
                for day in raw_day_plan
            ],
            frozen=planner_state.get("frozen_plan") is not None,
            awaiting=awaiting,
        )

    def _compose_matcher(self, matcher_state: dict[str, Any], has_recommendation: bool) -> TripViewMatcher:
        conversation = matcher_state.get("conversation_context") or {}
        return TripViewMatcher(
            last_message=conversation.get("last_meridian_message"),
            awaiting=conversation.get("awaiting"),
            has_recommendation=has_recommendation,
        )

    def _compose_booking(self, booking_setup: dict[str, Any]) -> TripViewBooking:
        party = booking_setup.get("party")
        return TripViewBooking(party=TravelerParty(**party) if isinstance(party, dict) and party else None)

    # ---- itinerary-derived (null until an itinerary exists) --------------

    def _compose_summary(
        self,
        final_itinerary: Optional[dict[str, Any]],
        trip_context: dict[str, Any],
        booking: TripViewBooking,
    ) -> Optional[TripViewSummary]:
        if final_itinerary is None:
            return None
        trip_summary = final_itinerary.get("trip_summary") or {}
        budget_summary = final_itinerary.get("budget_summary") or {}
        day_count = len(final_itinerary.get("days") or [])
        return TripViewSummary(
            title=trip_summary.get("title", ""),
            destinations=list(trip_summary.get("destinations") or []),
            duration_days=trip_summary.get("trip_duration", day_count),
            overview=trip_summary.get("overview", ""),
            route_rationale=trip_summary.get("route_rationale", ""),
            travelers=self._compose_travelers(trip_summary, trip_context, booking),
            dates=self._compose_dates(trip_context, day_count),
            budget=SummaryBudget(
                low=budget_summary.get("total_low", 0),
                high=budget_summary.get("total_high", 0),
                currency=budget_summary.get("currency", ""),
            ),
        )

    def _compose_travelers(
        self, trip_summary: dict[str, Any], trip_context: dict[str, Any], booking: TripViewBooking
    ) -> SummaryTravelers:
        if booking.party is not None:
            p = booking.party
            return SummaryTravelers(value=_party_label(p), exact=p, source="party")
        atlas_count = trip_summary.get("num_travelers")
        if isinstance(atlas_count, int) and atlas_count >= 1:
            return SummaryTravelers(value=f"~{atlas_count}", exact=None, source="itinerary_estimate")
        parsed = _parse_int(trip_context.get("num_travelers"))
        if parsed is not None:
            return SummaryTravelers(value=f"~{parsed}", exact=None, source="conversational")
        return SummaryTravelers(value=None, exact=None, source="unknown")

    def _compose_travel_window(self, trip_context: dict[str, Any]) -> Optional[TravelWindow]:
        """`GET /trips` list hint — the structured half of the composed trip
        dates (no itinerary in hand, so no day count and no computed return).
        `None` unless `travel_dates` parsed to a real calendar precision."""
        composed = compose_trip_dates(trip_context, 0)
        if composed.precision == "none":
            return None
        return TravelWindow(
            precision=composed.precision,
            departure=composed.departure,
            month=composed.month,
        )

    def _compose_dates(self, trip_context: dict[str, Any], day_count: int) -> SummaryDates:
        composed = compose_trip_dates(trip_context, day_count)
        return SummaryDates(
            precision=composed.precision,
            departure=composed.departure,
            return_=composed.return_,
            month=composed.month,
            label=composed.label,
            source=composed.source,
        )

    def _compose_budget_breakdown(
        self,
        final_itinerary: Optional[dict[str, Any]],
        trip_context: dict[str, Any],
        booking: TripViewBooking,
        summary: Optional[TripViewSummary],
    ) -> Optional[BudgetBreakdown]:
        if final_itinerary is None or summary is None:
            return None
        budget_summary = final_itinerary.get("budget_summary") or {}
        trip_summary = final_itinerary.get("trip_summary") or {}
        currency = budget_summary.get("currency", "")
        low = budget_summary.get("total_low", 0)
        high = budget_summary.get("total_high", 0)
        estimated_for = trip_summary.get("num_travelers")
        estimated_for = estimated_for if isinstance(estimated_for, int) else None

        party_total = booking.party.adults + booking.party.children + booking.party.infants if booking.party else None
        party_changed = bool(estimated_for is not None and party_total is not None and party_total != estimated_for)

        fit_note = _fit_note(low, high, currency, trip_context.get("budget"))
        if party_changed:
            fit_note += f" — these estimates were built for {estimated_for} travellers."

        return BudgetBreakdown(
            fit_note=fit_note,
            lines=[
                BudgetLine(
                    category=line.get("category", ""),
                    low=line.get("amount_low", 0),
                    high=line.get("amount_high", 0),
                    note=line.get("note", ""),
                )
                for line in budget_summary.get("lines") or []
            ],
            estimated_for_travelers=estimated_for,
            party_changed_since=party_changed,
        )

    def _compose_open_gaps(
        self, final_itinerary: Optional[dict[str, Any]], booking: TripViewBooking
    ) -> Optional[list[OpenGap]]:
        if final_itinerary is None:
            return None
        if booking.party is not None:
            return []
        return [
            OpenGap(
                what="who's travelling",
                resolution="set_party",
                detail="Set the exact party so every booking search asks for the right number of people.",
            )
        ]

    def _compose_before_you_go(
        self, final_itinerary: Optional[dict[str, Any]]
    ) -> Optional[list[BeforeYouGoItem]]:
        if final_itinerary is None:
            return None
        items: list[BeforeYouGoItem] = []
        for note in final_itinerary.get("practical_notes") or []:
            items.append(
                BeforeYouGoItem(
                    title=note.get("title", ""),
                    detail=note.get("detail", ""),
                    verify=bool(note.get("needs_verification")),
                )
            )
        for assumption in final_itinerary.get("assumptions") or []:
            category = assumption.get("category", "other")
            detail = assumption.get("detail", "")
            items.append(
                BeforeYouGoItem(
                    title=_ASSUMPTION_TITLES.get(category) or _lead(detail),
                    detail=detail,
                    verify=True,
                )
            )
        return items


# ---- pure helpers -------------------------------------------------------


def _coerce_display(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)


def _party_label(party: TravelerParty) -> str:
    parts = []
    for count, singular in ((party.adults, "adult"), (party.children, "child"), (party.infants, "infant")):
        if count:
            plural = {"adult": "adults", "child": "children", "infant": "infants"}[singular]
            parts.append(f"{count} {singular if count == 1 else plural}")
    return ", ".join(parts) or "1 adult"


def _parse_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            parsed = int(match.group())
            return parsed if parsed >= 1 else None
    return None


def _lead(detail: str) -> str:
    lead = re.split(r"(?<=[.!?])\s", detail.strip(), maxsplit=1)[0] if detail else ""
    return (lead[:80] or "Before you go").rstrip(".")


_CURRENCY_TOKEN = re.compile(r"[A-Za-z]{3}|[₹$€£¥]")
_NUMBER = re.compile(r"(\d[\d,]*\.?\d*)\s*([kK])?")


def _parse_budget(raw: Any, currency: str) -> Optional[int]:
    """A number only when it is confidently parseable AND its currency matches
    (or is absent from) the itinerary's currency — otherwise no comparison."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    tokens = {t.upper() for t in _CURRENCY_TOKEN.findall(raw)}
    if tokens:
        symbol = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
        named = {symbol.get(t, t) for t in tokens}
        if currency and currency.upper() not in named:
            return None
    match = _NUMBER.search(raw)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    if match.group(2):
        amount *= 1000
    return int(amount)


def _money(currency: str, amount: int) -> str:
    return f"{currency} {amount:,}".strip()


def _fit_note(low: int, high: int, currency: str, raw_budget: Any) -> str:
    ceiling = _parse_budget(raw_budget, currency)
    base = f"Estimated {_money(currency, low)}–{_money(currency, high)}."
    if ceiling is None:
        return base
    if high > ceiling:
        over = round((high - ceiling) / ceiling * 100)
        return f"{base} The higher end is ~{over}% over your stated {_money(currency, ceiling)} budget."
    return f"{base} Within your stated {_money(currency, ceiling)} budget."
