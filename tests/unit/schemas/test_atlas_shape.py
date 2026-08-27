import pytest
from pydantic import ValidationError

from twm.schemas.atlas import AtlasDay, AtlasTimelineItem


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
        "date": None,
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
