"""Tests for the reusable Meridian behavior-quality regression evaluator."""

from copy import deepcopy

from twm.quality.meridian_regression import evaluate_case, load_cases


EXPECTED_CASE_IDS = {
    "tc1-honeymoon-constraints-and-budget-boundary",
    "tc2-relative-monsoon-safety-and-short-trip-fit",
    "tc5-missing-road-trip-origin-needs-clarification",
    "tc5-road-trip-continuation-route-arithmetic",
}


def _case(case_id: str) -> dict:
    return next(case for case in load_cases() if case["id"] == case_id)


def _response(
    *,
    status: str,
    message: str,
    options: list[dict],
    trip_context: dict | None = None,
    awaiting: str | None = None,
) -> dict:
    return {
        "status": status,
        "message": message,
        "state_delta": {
            "trip_context": trip_context or {},
            "matcher_state": {
                "conversation_context": {
                    "last_meridian_message": message,
                    "awaiting": awaiting,
                }
            },
        },
        "generated_at": "2026-07-16T00:00:00Z",
        "trip_type": "circuit" if options else None,
        "options": options,
        "agent_meta": {"agent": "meridian", "prompt_version": "1.2.0"},
    }


def _consistent_circuit() -> dict:
    return {
        "rank": 1,
        "type": "circuit",
        "name": "Bengaluru and Karnataka monsoon circuit",
        "destination_id": None,
        "circuit_id": "bengaluru_karnataka_july",
        "best_for": "A four to five day July circuit by car with multiple stops",
        "why_ranked_here": [
            "Starts in Bengaluru and keeps the July driving plan inside Karnataka",
            "Balances multiple places with the available four to five days",
        ],
        "decision_summary": {
            "matches": ["Car friendly circuit from Bangalore"],
            "tradeoffs": ["Monsoon rain can slow hill-road driving"],
        },
        "sections": [
            {
                "type": "stops",
                "heading": "Stops",
                "stops": [
                    {"name": "Stop one", "nights": 2, "what_it_offers": "Nature"},
                    {"name": "Stop two", "nights": 2, "what_it_offers": "Coffee"},
                ],
            },
            {
                "type": "internal_travel",
                "heading": "Internal travel",
                "legs": [
                    {
                        "from": "Bengaluru",
                        "to": "Stop one",
                        "duration": "about 150 km and 3 hours",
                        "mode": "car",
                    },
                    {
                        "from": "Stop one",
                        "to": "Stop two",
                        "duration": "about 200 km and 4 hours",
                        "mode": "car",
                    },
                    {
                        "from": "Stop two",
                        "to": "Bengaluru",
                        "duration": "about 250 km and 5 hours",
                        "mode": "car",
                    },
                ],
            },
            {
                "type": "route",
                "heading": "Route feasibility",
                "points": [
                    "Total driving: about 600 km and 12 hours",
                    "Average daily driving: about 150 km and 3 hours across 4 driving days",
                ],
            },
            {
                "type": "season",
                "heading": "July conditions",
                "points": ["Monsoon rain can increase travel time"],
            },
        ],
    }


def test_manifest_covers_every_approved_meridian_gap_and_continuation() -> None:
    cases = load_cases()

    assert {case["id"] for case in cases} == EXPECTED_CASE_IDS
    assert all(case["expected"] for case in cases)
    assert all(case["semantic_rubric"] for case in cases)


def test_material_missing_origin_returns_one_safe_targeted_clarification() -> None:
    case = _case("tc5-missing-road-trip-origin-needs-clarification")
    output = _response(
        status="NEEDS_CLARIFICATION",
        message=(
            "July monsoon conditions can make a four to five day driving "
            "circuit highly dependent on the route. "
            "Which city will you start from?"
        ),
        options=[],
        awaiting="starting_city",
    )

    result = evaluate_case(case, output)

    assert result["passed"] is True
    assert result["raw_output"] == output
    assert result["agent_meta"] == {
        "agent": "meridian",
        "prompt_version": "1.2.0",
    }


def test_clarification_rejects_a_silently_inferred_origin() -> None:
    case = _case("tc5-missing-road-trip-origin-needs-clarification")
    output = _response(
        status="NEEDS_CLARIFICATION",
        message=(
            "July monsoon conditions can make a four to five day driving "
            "route depend on its start. "
            "Which city will you start from?"
        ),
        options=[],
        trip_context={"origin": "Bengaluru"},
        awaiting="starting_city",
    )

    result = evaluate_case(case, output)

    failed = {item["id"] for item in result["structural_checks"] if not item["passed"]}
    assert "forbidden_context_key_origin" in failed


def test_route_arithmetic_passes_when_legs_totals_and_daily_load_agree() -> None:
    case = _case("tc5-road-trip-continuation-route-arithmetic")
    output = _response(
        status="SUCCESS",
        message=(
            "These Bengaluru circuits account for July monsoon trade-offs. "
            "Check the current forecast, road status, closures, and official "
            "local advisories near departure."
        ),
        options=[_consistent_circuit()],
        trip_context={"starting_city": "Bengaluru"},
    )

    result = evaluate_case(case, output)

    assert result["passed"] is True


def test_route_arithmetic_detects_inconsistent_totals() -> None:
    case = _case("tc5-road-trip-continuation-route-arithmetic")
    circuit = _consistent_circuit()
    circuit["sections"][2]["points"][0] = "Total driving: about 400 km and 8 hours"
    output = _response(
        status="SUCCESS",
        message=(
            "This Bengaluru route accounts for July monsoon driving. Check the "
            "forecast, road status, closures, and official advisories near departure."
        ),
        options=[circuit],
        trip_context={"starting_city": "Bengaluru"},
    )

    result = evaluate_case(case, output)

    failed = {item["id"] for item in result["structural_checks"] if not item["passed"]}
    assert "option_1_route_distance_total" in failed
    assert "option_1_route_time_total" in failed


def test_evaluator_rejects_noncanonical_meridian_output() -> None:
    case = deepcopy(_case("tc1-honeymoon-constraints-and-budget-boundary"))

    result = evaluate_case(case, {"status": "SUCCESS", "version": "matcher_v2"})

    assert result["passed"] is False
    assert result["structural_checks"][0]["id"] == "canonical_response"
