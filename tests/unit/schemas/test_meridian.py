"""Semantic tests for the Meridian recommendation contract."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from twm.schemas import MeridianResponse, RecommendationOption


def valid_option(rank: int = 1) -> dict:
    return {
        "rank": rank,
        "type": "single",
        "name": "Mountain Haven",
        "destination_id": f"destination-{rank}",
        "verdict": "Best overall fit",
        "summary": "Balances the traveler's pace and budget.",
        "criteria": [
            {
                "id": "pace",
                "label": "Relaxed pace",
                "requirement_type": "PREFERENCE",
                "outcome": "MATCH",
                "summary": "The stay can be kept unhurried.",
                "details": [
                    {
                        "type": "bullets",
                        "items": ["Most activities fit within short travel days."],
                    },
                    {
                        "type": "facts",
                        "facts": [{"label": "Base changes", "value": "One"}],
                    },
                    {
                        "type": "note",
                        "text": "Current road conditions need verification near departure.",
                    },
                ],
            },
            {
                "id": "budget",
                "label": "Budget fit",
                "requirement_type": "HARD",
                "outcome": "TRADEOFF",
                "summary": "The upper estimate reaches the stated limit.",
                "details": [
                    {
                        "type": "cost_breakdown",
                        "currency": "INR",
                        "items": [
                            {
                                "label": "Stay",
                                "per_person": {"minimum": 6000, "maximum": 7500},
                                "group": {"minimum": 12000, "maximum": 15000},
                            }
                        ],
                        "per_person_total": {
                            "minimum": 10000,
                            "maximum": 12500,
                        },
                        "group_total": {"minimum": 20000, "maximum": 25000},
                    }
                ],
                "tradeoffs": ["Premium stays would exceed the limit."],
            },
        ],
    }


def terminal_state() -> dict:
    return {
        "matcher_state": {
            "conversation_context": {
                "last_meridian_message": "I found one suitable option.",
                "awaiting": None,
            }
        }
    }


def success_response(**overrides) -> dict:
    payload = {
        "status": "SUCCESS",
        "message": "I found one suitable option.",
        "state_delta": terminal_state(),
        "trip_type": "single",
        "options": [valid_option()],
        "agent_meta": {"agent": "meridian", "prompt_version": "test"},
    }
    payload.update(overrides)
    return payload


def test_dynamic_option_accepts_all_canonical_detail_blocks() -> None:
    response = MeridianResponse(**success_response())

    body = response.model_dump(mode="json", exclude_none=True)

    cost = body["options"][0]["criteria"][1]["details"][0]
    assert cost["currency"] == "INR"
    assert cost["per_person_total"] == {"minimum": 10000.0, "maximum": 12500.0}
    assert "circuit_id" not in body["options"][0]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda option: option.update({"best_for": "legacy"}),
        lambda option: option.update({"itinerary": [{"day": 1}]}),
        lambda option: option.update({"rank": 0}),
        lambda option: option.update({"circuit_id": "wrong-kind"}),
        lambda option: option["criteria"].append(deepcopy(option["criteria"][0])),
    ],
)
def test_option_rejects_legacy_malformed_or_duplicate_content(mutate) -> None:
    option = valid_option()
    mutate(option)

    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_costs_reject_invalid_ranges_and_missing_numeric_values() -> None:
    option = valid_option()
    cost = option["criteria"][1]["details"][0]
    cost["per_person_total"] = {"minimum": 10000, "maximum": 9000}

    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    cost.clear()
    cost.update({"type": "cost_breakdown", "currency": "INR"})
    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    option = valid_option()
    cost = option["criteria"][1]["details"][0]
    cost["currency"] = "inr"
    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    cost["currency"] = "INR"
    cost["group_total"] = {"minimum": 5000, "maximum": 6000}
    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_hard_mismatch_and_undisclosed_tradeoff_are_rejected() -> None:
    option = valid_option()
    budget = option["criteria"][1]
    budget["outcome"] = "MISMATCH"

    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    budget["requirement_type"] = "PREFERENCE"
    budget["tradeoffs"] = []
    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_preference_mismatch_is_allowed_when_disclosed() -> None:
    option = valid_option()
    pace = option["criteria"][0]
    pace["outcome"] = "MISMATCH"
    pace["tradeoffs"] = ["Daily transfers make the requested pace difficult."]

    RecommendationOption(**option)


def test_absent_cost_totals_remain_absent() -> None:
    option = valid_option()
    cost = option["criteria"][1]["details"][0]
    cost.pop("per_person_total")
    cost.pop("group_total")

    body = RecommendationOption(**option).model_dump(mode="json", exclude_none=True)

    cost_body = body["criteria"][1]["details"][0]
    assert "per_person_total" not in cost_body
    assert "group_total" not in cost_body


def test_success_requires_valid_sequential_options_and_terminal_state() -> None:
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(options=[]))

    with pytest.raises(ValidationError):
        MeridianResponse(
            **success_response(options=[valid_option(1), valid_option(3)])
        )

    invalid_state = terminal_state()
    invalid_state["matcher_state"]["conversation_context"]["awaiting"] = "budget"
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(state_delta=invalid_state))


def test_ranked_options_require_unique_identity_and_aligned_criteria() -> None:
    duplicate = valid_option(2)
    duplicate["destination_id"] = "destination-1"
    with pytest.raises(ValidationError):
        MeridianResponse(
            **success_response(options=[valid_option(1), duplicate])
        )

    different_criteria = valid_option(2)
    different_criteria["criteria"].pop()
    with pytest.raises(ValidationError):
        MeridianResponse(
            **success_response(options=[valid_option(1), different_criteria])
        )


def test_option_rejects_mixed_cost_currencies() -> None:
    option = valid_option()
    option["criteria"][0]["details"].append(
        {
            "type": "cost_breakdown",
            "currency": "USD",
            "per_person_total": {"minimum": 100, "maximum": 150},
        }
    )

    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_clarification_requires_one_awaiting_value_and_matching_message() -> None:
    payload = {
        "status": "NEEDS_CLARIFICATION",
        "message": "What budget should I use?",
        "state_delta": {
            "matcher_state": {
                "conversation_context": {
                    "last_meridian_message": "What budget should I use?",
                    "awaiting": "budget",
                }
            }
        },
        "options": [],
        "agent_meta": {"agent": "meridian", "prompt_version": "test"},
    }
    MeridianResponse(**payload)

    payload["state_delta"]["matcher_state"]["conversation_context"][
        "last_meridian_message"
    ] = "Different message"
    with pytest.raises(ValidationError):
        MeridianResponse(**payload)


def test_soft_fail_requires_visible_option_tradeoff() -> None:
    option = valid_option()
    option["criteria"] = [option["criteria"][0]]

    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(status="SOFT_FAIL", options=[option]))

    option["tradeoffs"] = ["The longer transfer reduces time at the destination."]
    MeridianResponse(**success_response(status="SOFT_FAIL", options=[option]))
