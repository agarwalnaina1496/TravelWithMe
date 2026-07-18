"""Structural checks for the reusable security regression corpus."""

import json
from pathlib import Path


CORPUS = Path(__file__).parents[1] / "resources" / "adversarial_turns.json"


def test_adversarial_corpus_covers_each_untrusted_boundary() -> None:
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))

    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["surface"] for case in cases} >= {
        "message",
        "trip_state",
        "prior_output",
        "retrieved_content",
        "matcher_state",
    }
    assert {case["kind"] for case in cases} >= {
        "injection",
        "encoded_injection",
        "indirect_injection",
        "multi_turn_injection",
        "off_topic",
        "mixed_context",
        "legitimate_travel",
    }
    assert any(case["travel_content"] for case in cases)
    assert any(not case["travel_content"] for case in cases)
