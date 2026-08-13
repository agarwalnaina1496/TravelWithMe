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
        "missing-duration-start",
        "missing-duration-approve-places",
    }
    assert cases_by_id["approve-rishikesh-places"]["invariants"] == {
        "phase": "DAY_PLAN_DRAFT",
        "duration_days": 3,
        "day_plan_length": 3,
        "preserve_all_places": True,
        "place_only_day_plan": True,
        "requires_day_pace": True,
    }


def test_guide_evaluation_corpus_covers_missing_duration_clarification() -> None:
    cases = json.loads(
        (ROOT / "tests" / "resources" / "guide_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    for case_id in ("missing-duration-start", "missing-duration-approve-places"):
        invariants = cases_by_id[case_id]["invariants"]
        assert invariants["phase"] == "NEEDS_CLARIFICATION"
        assert invariants["day_plan_length"] == 0
