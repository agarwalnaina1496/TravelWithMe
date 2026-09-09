"""TWM-227: the deterministic one-time seed of booking_setup.party from the
verbatim trip_context.num_travelers, run at plan-freeze."""

import pytest

from twm.services.trip_commands.party_seed import (
    parse_headcount,
    seed_party_from_num_travelers,
)
from twm.telemetry import TelemetryLogger
from twm.telemetry.settings import PayloadMode, TelemetrySettings
from twm.telemetry.sinks import InMemorySink


def _logger() -> tuple[TelemetryLogger, InMemorySink]:
    sink = InMemorySink()
    return TelemetryLogger(TelemetrySettings(True, "test", PayloadMode.METADATA, 16_384), sink), sink


def _state(num_travelers=None, party=None) -> dict:
    booking_setup = {}
    if party is not None:
        booking_setup["party"] = party
    return {
        "trip_id": "trip-1",
        "trip_context": {} if num_travelers is None else {"num_travelers": num_travelers},
        "booking_setup": booking_setup,
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3", 3),
        (3, None),  # not a string — the loose fact is stored as text
        ("3 travellers", 3),
        ("3 of us", 3),
        ("party of 4", 4),
        ("just the 2 of us", 2),
        ("couple", 2),
        ("just me", 1),
        ("solo traveller", 1),
        ("three friends", 3),
        ("two", 2),
        ("a big group", None),
        ("", None),
        ("  ", None),
        ("12", None),  # above the 9-person booking cap
        ("0", None),
        (None, None),
    ],
)
def test_parse_headcount(raw, expected):
    assert parse_headcount(raw) == expected


def test_seeds_party_when_unset_and_headcount_reads():
    logger, sink = _logger()
    state = _state(num_travelers="3 travellers")

    seed_party_from_num_travelers(logger, state)

    assert state["booking_setup"]["party"] == {"adults": 3, "children": 0, "infants": 0}
    seeded = [e for e in sink.events if e["event"] == "be.trip.booking_setup.party.seeded"]
    assert len(seeded) == 1
    assert seeded[0]["fields"]["adults"] == 3
    assert seeded[0]["fields"]["source_field"] == "num_travelers"
    assert "headcount" in seeded[0]["message"].lower()


def test_no_overwrite_when_party_already_edited():
    logger, sink = _logger()
    state = _state(num_travelers="5 people", party={"adults": 2, "children": 1, "infants": 0})

    seed_party_from_num_travelers(logger, state)

    assert state["booking_setup"]["party"] == {"adults": 2, "children": 1, "infants": 0}
    skipped = [e for e in sink.events if e["event"] == "be.trip.booking_setup.party.seed_skipped"]
    assert skipped and skipped[0]["fields"]["reason"] == "party_already_set"


def test_no_seed_when_headcount_unresolved():
    logger, sink = _logger()
    state = _state(num_travelers="not sure yet, a few of us")

    seed_party_from_num_travelers(logger, state)

    assert "party" not in state["booking_setup"]
    skipped = [e for e in sink.events if e["event"] == "be.trip.booking_setup.party.seed_skipped"]
    assert skipped and skipped[0]["fields"]["reason"] == "headcount_unresolved"


def test_no_seed_when_num_travelers_absent():
    logger, _ = _logger()
    state = _state()

    seed_party_from_num_travelers(logger, state)

    assert "party" not in state["booking_setup"]


def test_empty_party_dict_is_treated_as_unset():
    logger, _ = _logger()
    state = _state(num_travelers="4", party={})

    seed_party_from_num_travelers(logger, state)

    assert state["booking_setup"]["party"] == {"adults": 4, "children": 0, "infants": 0}
