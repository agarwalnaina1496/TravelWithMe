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
    }
    assert cases_by_id["criteria-ready-routes-to-matcher"]["invariants"] == {
        "intent": "matcher",
        "must_clear_selected_option": True,
        "routes_to_meridian": True,
    }
