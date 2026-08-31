import pytest
from pydantic import ValidationError

from twm.schemas.atlas import AtlasAgentOutput, AtlasDay, AtlasFinalItinerary, AtlasTimelineItem


def _reference() -> dict[str, str]:
    return {"status": "GENERAL_GUIDANCE"}


def _timeline_item(**overrides):
    item = {
        "kind": "TRAVEL",
        "title": "Travel from Delhi to Agra",
        "location": "Delhi to Agra",
        "detail": "Travel between the two cities.",
        "from_city": "Delhi",
        "to_city": "Agra",
        "reference": _reference(),
    }
    item.update(overrides)
    return item


def _day(**overrides):
    day = {
        "day_number": 1,
        "title": "Agra day",
        "primary_location": "Agra",
        "summary": "A practical day in Agra.",
        "timeline": [_timeline_item()],
        "notes": [
            {
                "category": "Timing",
                "title": "Taj Mahal Friday closure",
                "detail": "Plan monument timing around the weekly closure.",
                "reference": _reference(),
            }
        ],
        "backup_plan": None,
    }
    day.update(overrides)
    return day


def test_atlas_timeline_item_rejects_removed_travel_narrative_fields():
    for removed_field in ("from_place", "to_place", "display_label"):
        with pytest.raises(ValidationError):
            AtlasTimelineItem.model_validate(
                _timeline_item(**{removed_field: "Narrative copy"})
            )


def test_atlas_day_uses_notes_instead_of_legacy_guidance_fields():
    validated = AtlasDay.model_validate(_day())

    assert validated.notes[0].title == "Taj Mahal Friday closure"

    for removed_field in ("seasonal_guidance", "permit_or_ticket_guidance"):
        with pytest.raises(ValidationError):
            AtlasDay.model_validate(_day(**{removed_field: "Legacy guidance."}))


def test_day_specific_taj_closure_is_not_duplicated_in_trip_practical_notes():
    output = AtlasAgentOutput.model_validate(
        {
            "final_itinerary": {
                "trip_summary": {
                    "title": "Agra weekend",
                    "destinations": ["Agra"],
                    "trip_duration": 1,
                    "num_travelers": 2,
                    "date_range": None,
                    "overview": "A short visit to Agra.",
                    "route_rationale": "One base keeps the weekend practical.",
                },
                "days": [_day()],
                "budget_summary": {
                    "currency": "INR",
                    "lines": [
                        {
                            "category": "Activities",
                            "amount_low": 1000,
                            "amount_high": 2000,
                            "note": "Allow for monument entry fees.",
                        }
                    ],
                    "budget_fit": "Within the stated budget.",
                },
                "practical_notes": [
                    {
                        "category": "Emergency planning",
                        "title": "Keep trip-wide contacts available",
                        "detail": "Save accommodation and emergency contacts offline.",
                        "reference": _reference(),
                    }
                ],
                "sources": [],
                "assumptions": [],
            },
            "unresolved": [],
        }
    )
    itinerary = output.final_itinerary

    day_note_titles = [note.title for note in itinerary.days[0].notes]
    practical_note_titles = [note.title for note in itinerary.practical_notes]
    unresolved_items = [item.item for item in output.unresolved]

    assert day_note_titles == ["Taj Mahal Friday closure"]
    assert practical_note_titles == ["Keep trip-wide contacts available"]
    assert "Taj Mahal Friday closure" not in practical_note_titles
    # Atlas 1.12.0: the same anti-duplication rule now also covers
    # `unresolved` -- a fact already given a confident home in day.notes
    # must not also be flagged as unresolved.
    assert "Taj Mahal Friday closure" not in unresolved_items


def test_day_specific_advance_booking_guidance_for_different_monuments_is_split_by_day():
    # Atlas 1.12.0: two monuments visited on different days must each get
    # their own day-specific note, not one merged whole-trip practical_notes
    # item covering both (live-testing finding: a single "Advance Monument
    # Booking" note collapsed Qutub Minar's and Taj Mahal's guidance
    # together regardless of which day each was actually visited).
    qutub_day = _day(
        day_number=1,
        title="Delhi day",
        primary_location="Delhi",
        notes=[
            {
                "category": "Tickets",
                "title": "Book Qutub Minar entry ahead",
                "detail": "Advance booking recommended for Qutub Minar on this day.",
                "reference": _reference(),
            }
        ],
    )
    taj_day = _day(
        day_number=2,
        title="Agra day",
        primary_location="Agra",
        notes=[
            {
                "category": "Tickets",
                "title": "Book Taj Mahal entry ahead",
                "detail": "Advance booking recommended for Taj Mahal on this day.",
                "reference": _reference(),
            }
        ],
    )
    itinerary = AtlasFinalItinerary.model_validate(
        {
            "trip_summary": {
                "title": "Delhi-Agra weekend",
                "destinations": ["Delhi", "Agra"],
                "trip_duration": 2,
                "num_travelers": 2,
                "date_range": None,
                "overview": "A short Delhi-Agra trip.",
                "route_rationale": "Two bases keep travel practical.",
            },
            "days": [qutub_day, taj_day],
            "budget_summary": {
                "currency": "INR",
                "lines": [
                    {
                        "category": "Activities",
                        "amount_low": 1000,
                        "amount_high": 2000,
                        "note": "Allow for monument entry fees.",
                    }
                ],
                "budget_fit": "Within the stated budget.",
            },
            "practical_notes": [],
            "sources": [],
            "assumptions": [],
        }
    )

    day_one_titles = [note.title for note in itinerary.days[0].notes]
    day_two_titles = [note.title for note in itinerary.days[1].notes]
    practical_note_titles = [note.title for note in itinerary.practical_notes]

    assert day_one_titles == ["Book Qutub Minar entry ahead"]
    assert day_two_titles == ["Book Taj Mahal entry ahead"]
    # Neither monument's guidance is merged into one whole-trip note.
    assert practical_note_titles == []
