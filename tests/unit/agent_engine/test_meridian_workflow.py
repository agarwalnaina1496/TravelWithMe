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
        "group-total-budget-affordability-prioritizes-plausible-access",
        "per-person-budget-interpreted-as-stated",
        "missing-origin-blocks-affordability-clarification",
        "unaffordable-transport-mode-excluded-without-hardcoded-ban",
        "circuit-accounts-for-complete-round-trip-cost",
    }
    assert cases_by_id["circuit-preference-clarification"]["invariants"] == {
        "status": "NEEDS_CLARIFICATION",
        "requires_awaiting_reason": True,
        "message_matches_last_meridian_message": True,
        "no_options_allowed": True,
    }
    assert cases_by_id["group-total-budget-affordability-prioritizes-plausible-access"][
        "invariants"
    ]["no_hardcoded_transport_mode_exclusion"] is True
    assert cases_by_id["missing-origin-blocks-affordability-clarification"][
        "invariants"
    ]["status"] == "NEEDS_CLARIFICATION"
    assert cases_by_id["circuit-accounts-for-complete-round-trip-cost"][
        "invariants"
    ]["requires_complete_round_trip_accounting"] is True
