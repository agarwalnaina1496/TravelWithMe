"""Meridian behavioral evaluation-corpus checks."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_meridian_evaluation_corpus_covers_status_and_state_ownership() -> None:
    cases = json.loads(
        (ROOT / "tests" / "resources" / "meridian_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert set(cases_by_id) == {
        "goa-single-destination-success",
        "circuit-preference-clarification",
        "budget-conflict-soft-fail",
        "recommendation-history-is-backend-owned",
    }
    assert cases_by_id["circuit-preference-clarification"]["invariants"] == {
        "status": "NEEDS_CLARIFICATION",
        "requires_awaiting_reason": True,
        "message_matches_last_meridian_message": True,
        "no_options_allowed": True,
    }
