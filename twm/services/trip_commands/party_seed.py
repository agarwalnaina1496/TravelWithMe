"""TWM-227: deterministic one-time seed of ``booking_setup.party`` from the
verbatim ``trip_context.num_travelers`` conversational fact.

Agents cannot write ``booking_setup`` (TWM-223 write-boundary). At plan-freeze
— the deterministic ``APPROVE_PLAN`` transition, before Atlas — this bridges a
clearly-stated headcount into the structured booking party so the first
booking redirect asks for the right number of people without the traveler
opening a drawer. It is a one-time seed: it never runs when a party is already
set (the traveler may have edited it), and a headcount it cannot confidently
read leaves the party unset for booking's safe 1-adult fallback.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ...schemas.booking_setup import TravelerComposition
from ...telemetry import TelemetryLogger

_SEED_EVENT = "be.trip.booking_setup.party.seeded"
_SKIP_EVENT = "be.trip.booking_setup.party.seed_skipped"

# "couple", "solo" and friends — a headcount stated in words, not digits.
_PHRASE_COUNTS: tuple[tuple[str, int], ...] = (
    ("just me", 1),
    ("by myself", 1),
    ("on my own", 1),
    ("solo", 1),
    ("alone", 1),
    ("couple", 2),
    ("the two of us", 2),
    ("both of us", 2),
)
_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_INT_RE = re.compile(r"\b(\d{1,2})\b")
_MAX_PARTY = 9


def parse_headcount(raw: Any) -> Optional[int]:
    """A bounded best-effort read of the loose ``num_travelers`` string.
    Returns a 1–9 count, or ``None`` when nothing confidently reads as one."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    if not text:
        return None

    match = _INT_RE.search(text)
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= _MAX_PARTY else None

    for phrase, count in _PHRASE_COUNTS:
        if phrase in text:
            return count

    for word, count in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return count

    return None


def _existing_party(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    branch = state.get("booking_setup")
    if not isinstance(branch, dict):
        return None
    party = branch.get("party")
    return party if isinstance(party, dict) and party else None


def seed_party_from_num_travelers(logger: TelemetryLogger, state: dict[str, Any]) -> None:
    """Seed ``booking_setup.party`` once, at plan-freeze. Idempotent: a no-op
    when a party already exists or when no headcount can be read."""
    trip_id = str(state["trip_id"]) if state.get("trip_id") else None

    if _existing_party(state) is not None:
        logger.info(
            "Skipped seeding the traveler party — the traveler already set one.",
            event=_SKIP_EVENT,
            source="application",
            trip_id=trip_id,
            reason="party_already_set",
        )
        return

    raw = (state.get("trip_context") or {}).get("num_travelers")
    adults = parse_headcount(raw)
    if adults is None:
        logger.info(
            "Skipped seeding the traveler party — no headcount read from num_travelers.",
            event=_SKIP_EVENT,
            source="application",
            trip_id=trip_id,
            reason="headcount_unresolved",
        )
        return

    party = TravelerComposition(adults=adults)
    branch = state.setdefault("booking_setup", {})
    if not isinstance(branch, dict):
        branch = {}
        state["booking_setup"] = branch
    branch["party"] = party.model_dump(mode="json")

    logger.info(
        "Seeded the structured traveler party from the stated headcount.",
        event=_SEED_EVENT,
        source="application",
        trip_id=trip_id,
        source_field="num_travelers",
        adults=party.adults,
        children=party.children,
        infants=party.infants,
    )
