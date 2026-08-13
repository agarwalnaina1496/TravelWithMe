"""Scout behavioral evaluation-corpus checks."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_scout_evaluation_corpus_covers_extraction_and_routing() -> None:
    cases = json.loads(
        (ROOT / "tests" / "resources" / "scout_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert set(cases_by_id) == {
        "advisor-answers-general-question",
        "criteria-ready-routes-to-matcher",
        "destination-confirmed-routes-to-planner",
        "extraction-preserves-advisor-message",
        "extracts-fixed-shared-keys-verbatim",
    }
    assert cases_by_id["criteria-ready-routes-to-matcher"]["invariants"] == {
        "intent": "matcher",
        "must_clear_selected_option": True,
        "routes_to_meridian": True,
    }


def test_scout_evaluation_corpus_covers_fixed_shared_keys() -> None:
    cases = json.loads(
        (ROOT / "tests" / "resources" / "scout_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    invariants = cases_by_id["extracts-fixed-shared-keys-verbatim"]["invariants"]
    assert invariants["fixed_keys_present_verbatim"] == {
        "origin_city": "Delhi",
        "num_travelers": "me and my partner",
        "trip_duration": 5,
        "travel_dates": "sometime in October",
        "budget": "flexible",
    }
    assert "origin" in invariants["must_not_invent_synonym_keys"]
