"""Semantic tests for the Meridian recommendation contract."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from twm.schemas import MeridianResponse, RecommendationOption


def valid_catalog() -> list[dict]:
    return [
        {
            "id": "pace",
            "label": "Relaxed pace",
            "requirement_type": "PREFERENCE",
            "source_context_paths": ["travel_style.pace"],
        },
        {
            "id": "budget",
            "label": "Budget excluding tickets",
            "requirement_type": "HARD",
            "source_context_paths": ["budget", "budget_inclusions"],
        },
    ]


def valid_option(rank: int = 1) -> dict:
    return {
        "rank": rank,
        "type": "single",
        "name": f"Mountain Haven {rank}",
        "destination_id": f"destination-{rank}",
        "summary": "Balances the traveler's pace and budget.",
        "evaluations": [
            {
                "criterion_id": "pace",
                "outcome": "MATCH",
                "conclusion": "The stay can be kept unhurried.",
                "details": [
                    {
                        "type": "bullets",
                        "items": ["Most activities fit within short travel days."],
                    },
                    {
                        "type": "facts",
                        "facts": [{"label": "Base changes", "value": "One"}],
                    },
                ],
            },
            {
                "criterion_id": "budget",
                "outcome": "TRADEOFF",
                "conclusion": "The upper estimate reaches the stated limit.",
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
        "criteria_catalog": valid_catalog(),
        "options": [valid_option()],
        "agent_meta": {"agent": "meridian", "prompt_version": "test"},
    }
    payload.update(overrides)
    return payload


def test_dynamic_response_accepts_all_canonical_detail_blocks() -> None:
    response = MeridianResponse(**success_response())

    body = response.model_dump(mode="json", exclude_none=True)

    cost = body["options"][0]["evaluations"][1]["details"][0]
    assert cost["currency"] == "INR"
    assert cost["per_person_total"] == {"minimum": 10000.0, "maximum": 12500.0}
    assert "circuit_id" not in body["options"][0]
    assert body["criteria_catalog"][0]["source_context_paths"] == [
        "travel_style.pace"
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda option: option.update({"best_for": "legacy"}),
        lambda option: option.update({"verdict": "legacy"}),
        lambda option: option.update({"itinerary": [{"day": 1}]}),
        lambda option: option.update({"rank": 0}),
        lambda option: option.update({"circuit_id": "wrong-kind"}),
        lambda option: option["evaluations"].append(
            deepcopy(option["evaluations"][0])
        ),
    ],
)
def test_option_rejects_legacy_malformed_or_duplicate_content(mutate) -> None:
    option = valid_option()
    mutate(option)

    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_note_detail_block_is_not_part_of_the_contract() -> None:
    option = valid_option()
    option["evaluations"][0]["details"] = [
        {"type": "note", "text": "Free-form note"}
    ]

    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_costs_reject_invalid_ranges_and_missing_numeric_values() -> None:
    option = valid_option()
    cost = option["evaluations"][1]["details"][0]
    cost["per_person_total"] = {"minimum": 10000, "maximum": 9000}
    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    cost.clear()
    cost.update({"type": "cost_breakdown", "currency": "INR"})
    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    option = valid_option()
    cost = option["evaluations"][1]["details"][0]
    cost["currency"] = "inr"
    with pytest.raises(ValidationError):
        RecommendationOption(**option)

    cost["currency"] = "INR"
    cost["group_total"] = {"minimum": 5000, "maximum": 6000}
    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_hard_mismatch_and_undisclosed_tradeoff_are_rejected() -> None:
    option = valid_option()
    budget = option["evaluations"][1]
    budget["outcome"] = "MISMATCH"
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(options=[option]))

    catalog = valid_catalog()
    catalog[1]["requirement_type"] = "PREFERENCE"
    budget["tradeoffs"] = []
    with pytest.raises(ValidationError):
        MeridianResponse(
            **success_response(criteria_catalog=catalog, options=[option])
        )


def test_preference_mismatch_is_allowed_when_disclosed() -> None:
    option = valid_option()
    pace = option["evaluations"][0]
    pace["outcome"] = "MISMATCH"
    pace["tradeoffs"] = ["Daily transfers make the requested pace difficult."]

    MeridianResponse(**success_response(options=[option]))


def test_absent_cost_totals_remain_absent() -> None:
    option = valid_option()
    cost = option["evaluations"][1]["details"][0]
    cost.pop("per_person_total")
    cost.pop("group_total")

    body = RecommendationOption(**option).model_dump(mode="json", exclude_none=True)
    cost_body = body["evaluations"][1]["details"][0]
    assert "per_person_total" not in cost_body
    assert "group_total" not in cost_body


def test_success_requires_catalog_options_ranks_and_terminal_state() -> None:
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(criteria_catalog=[]))
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


def test_catalog_requires_unique_ids_labels_and_source_paths() -> None:
    catalog = valid_catalog()
    duplicate = deepcopy(catalog[0])
    duplicate["source_context_paths"] = ["another.path"]
    catalog.append(duplicate)
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(criteria_catalog=catalog))

    catalog = valid_catalog()
    catalog[1]["label"] = catalog[0]["label"]
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(criteria_catalog=catalog))

    catalog = valid_catalog()
    catalog[1]["source_context_paths"] = ["travel_style.pace"]
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(criteria_catalog=catalog))

    catalog = valid_catalog()
    catalog[1]["source_context_paths"] = ["not a valid path"]
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(criteria_catalog=catalog))


def test_options_require_unique_identity_and_exact_catalog_coverage() -> None:
    duplicate = valid_option(2)
    duplicate["destination_id"] = "destination-1"
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(options=[valid_option(1), duplicate]))

    missing = valid_option(2)
    missing["evaluations"].pop()
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(options=[valid_option(1), missing]))

    unknown = valid_option()
    unknown["evaluations"][0]["criterion_id"] = "unknown"
    with pytest.raises(ValidationError):
        MeridianResponse(**success_response(options=[unknown]))


def test_option_rejects_mixed_cost_currencies() -> None:
    option = valid_option()
    option["evaluations"][0]["details"].append(
        {
            "type": "cost_breakdown",
            "currency": "USD",
            "per_person_total": {"minimum": 100, "maximum": 150},
        }
    )

    with pytest.raises(ValidationError):
        RecommendationOption(**option)


def test_clarification_requires_awaiting_message_and_forbids_catalog() -> None:
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

    payload["criteria_catalog"] = valid_catalog()
    with pytest.raises(ValidationError):
        MeridianResponse(**payload)
    payload.pop("criteria_catalog")

    payload["state_delta"]["matcher_state"]["conversation_context"][
        "last_meridian_message"
    ] = "Different message"
    with pytest.raises(ValidationError):
        MeridianResponse(**payload)


def test_soft_fail_requires_visible_tradeoff() -> None:
    option = valid_option()
    option["evaluations"] = [option["evaluations"][0]]
    catalog = [valid_catalog()[0]]

    with pytest.raises(ValidationError):
        MeridianResponse(
            **success_response(
                status="SOFT_FAIL",
                criteria_catalog=catalog,
                options=[option],
            )
        )

    option["other_considerations"] = [
        "The longer transfer reduces time at the destination."
    ]
    MeridianResponse(
        **success_response(
            status="SOFT_FAIL", criteria_catalog=catalog, options=[option]
        )
    )
