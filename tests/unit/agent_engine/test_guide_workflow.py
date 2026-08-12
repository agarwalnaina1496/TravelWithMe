"""Guide behavioral evaluation-corpus checks."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_guide_evaluation_corpus_covers_incremental_planning() -> None:
    cases = json.loads(
        (ROOT / "tests" / "resources" / "guide_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert set(cases_by_id) == {
        "rishikesh-start",
        "remove-rafting-add-pilgrimage",
        "approve-rishikesh-places",
        "remove-place-shortens-day-explains-tradeoff",
        "preserve-explicit-circuit",
    }
    assert cases_by_id["approve-rishikesh-places"]["invariants"] == {
        "phase": "DAY_PLAN_DRAFT",
        "duration_days": 3,
        "day_plan_length": 3,
        "preserve_all_places": True,
        "place_only_day_plan": True,
        "requires_day_pace": True,
    }
