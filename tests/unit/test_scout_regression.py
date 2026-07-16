"""Tests for the reusable Scout behavior-quality regression evaluator."""

from copy import deepcopy

from twm.quality.scout_regression import evaluate_case, load_cases


EXPECTED_CASE_IDS = {
    "tc1-nationality-is-not-origin",
    "tc2-relative-safety-and-destination-type",
    "tc3-comparison-verdict-and-pacing",
    "tc4-route-specific-seasonal-advice",
    "tc5-current-season-and-multi-stop-intent",
}


def _case(case_id: str) -> dict:
    return next(case for case in load_cases() if case["id"] == case_id)


def _response(context: dict, message: str, intent: str) -> dict:
    return {
        "message": message,
        "state_delta": {"trip_context": context},
        "intent": intent,
        "agent_meta": {"agent": "scout", "prompt_version": "1.3.0"},
    }


def test_manifest_maps_every_approved_scout_case_to_checks_and_rubrics() -> None:
    cases = load_cases()

    assert {case["id"] for case in cases} == EXPECTED_CASE_IDS
    assert all(case["expected"] for case in cases)
    assert all(case["semantic_rubric"] for case in cases)


def test_route_specific_advice_passes_structural_checks_and_keeps_provenance() -> None:
    case = _case("tc4-route-specific-seasonal-advice")
    output = _response(
        {
            "destination": "Spiti Valley",
            "timing_comparison": ["late July/August", "September"],
            "destination_conditions": "Spiti itself does not receive much rainfall",
            "approach_route_concerns": [
                "roads leading to Spiti",
                "monsoon",
                "landslides",
                "water crossings",
            ],
        },
        (
            "July and August can mean more monsoon exposure on the approach roads, while September often has lower seasonal disruption risk in Spiti. "
            "Route and road conditions can still change, so check the current forecast, official road status or closure advisories near departure, and keep a buffer day."
        ),
        "advise",
    )

    result = evaluate_case(case, output)

    assert result["passed"] is True
    assert result["agent_meta"] == {
        "agent": "scout",
        "prompt_version": "1.3.0",
    }
    assert result["raw_output"] == output
    assert result["semantic_rubric"] == case["semantic_rubric"]


def test_evaluator_detects_inferred_origin_and_missing_extraction() -> None:
    case = _case("tc1-nationality-is-not-origin")
    output = _response(
        {"nationality": "Indians", "origin": "India", "trip_purpose": "honeymoon"},
        "",
        "matcher",
    )

    result = evaluate_case(case, output)

    assert result["passed"] is False
    failed = {check["id"] for check in result["structural_checks"] if not check["passed"]}
    assert "forbidden_context_key_origin" in failed
    assert any(check_id.startswith("context_value_") for check_id in failed)


def test_evaluator_rejects_noncanonical_raw_output() -> None:
    case = deepcopy(_case("tc3-comparison-verdict-and-pacing"))

    result = evaluate_case(case, {"message": "Incomplete"})

    assert result["passed"] is False
    assert result["structural_checks"][0]["id"] == "canonical_response"


def test_evaluator_rejects_wrong_agent_provenance() -> None:
    case = _case("tc4-route-specific-seasonal-advice")
    output = _response({}, "Incomplete", "advise")
    output["agent_meta"]["agent"] = "meridian"

    result = evaluate_case(case, output)

    assert result["passed"] is False
    failed = {check["id"] for check in result["structural_checks"] if not check["passed"]}
    assert "agent_provenance" in failed
