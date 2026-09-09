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
        "more-like-this-plain-single-reference",
        "more-like-this-qualified-circuit-reference",
        "circuit-return-timing-feasible-match",
        "circuit-return-timing-infeasible-tradeoff",
        "circuit-no-return-timing-constraint-unaffected",
        "multi-origin-meeting-point-success",
        "multi-origin-does-not-reask-origin-clarification",
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


def test_meridian_evaluation_corpus_covers_multi_origin_meeting_points() -> None:
    # TWM-214: a group stating several origins never re-triggers the
    # origin_city gate, and a "where can we all meet" ask returns ranked
    # options; origin_city is only ever written as a single scalar.
    cases = json.loads(
        (ROOT / "tests" / "resources" / "meridian_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert cases_by_id["multi-origin-meeting-point-success"]["invariants"] == {
        "status": "SUCCESS",
        "trip_type": "single",
        "requires_traveler_criteria": True,
        "requires_ranked_options": True,
        "awaiting_cleared": True,
        "origin_gate_not_blocked": True,
        "origin_city_single_valued": True,
        "options_compatible_with_all_origins": True,
        "meeting_point_framing": True,
    }
    assert cases_by_id["multi-origin-does-not-reask-origin-clarification"][
        "invariants"
    ]["origin_gate_not_blocked"] is True
