"""Reusable evaluation for recorded Scout behavior-regression runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..schemas.scout import ScoutResponse
from .common import all_keys, check, contains_any, flatten_values, load_case_manifest


DEFAULT_CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "scout_quality_cases.json"
)


def load_cases(path: Path = DEFAULT_CASES_FILE) -> list[dict[str, Any]]:
    """Load and validate the reusable Scout behavior-case manifest."""

    return load_case_manifest(path, "Scout quality")


def evaluate_case(
    case: dict[str, Any], raw_output: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate deterministic checks and preserve the manual semantic rubric."""

    case_id = case["id"]
    try:
        response = ScoutResponse.model_validate(raw_output)
    except ValidationError as exc:
        return {
            "case_id": case_id,
            "passed": False,
            "raw_output": raw_output,
            "agent_meta": raw_output.get("agent_meta"),
            "structural_checks": [
                check("canonical_response", False, str(exc))
            ],
            "semantic_rubric": case["semantic_rubric"],
        }

    output = response.model_dump(mode="json")
    expected = case["expected"]
    context = output["state_delta"]["trip_context"]
    context_text = "\n".join(flatten_values(context))
    message = output.get("message") or ""
    checks = [
        check("canonical_response", True, "Response matches ScoutResponse"),
        check(
            "agent_provenance",
            output["agent_meta"]["agent"] == "scout",
            f"expected 'scout', got {output['agent_meta']['agent']!r}",
        ),
        check(
            "intent",
            output.get("intent") == expected.get("intent"),
            f"expected {expected.get('intent')!r}, got {output.get('intent')!r}",
        ),
    ]

    message_mode = expected.get("message_mode")
    if message_mode == "empty":
        checks.append(
            check(
                "message_empty",
                not message.strip(),
                "Matcher/Planner handoff message is empty",
            )
        )
    elif message_mode == "non_empty":
        checks.append(
            check(
                "message_non_empty",
                bool(message.strip()),
                "Visible Scout response is present",
            )
        )

    for index, alternatives in enumerate(expected.get("required_context_value_groups", []), 1):
        checks.append(
            check(
                f"context_value_{index}",
                contains_any(context_text, alternatives),
                f"expected one of {alternatives!r} in extracted context",
            )
        )

    context_keys = all_keys(context)
    for key in expected.get("forbidden_context_keys", []):
        checks.append(
            check(
                f"forbidden_context_key_{key}",
                key.casefold() not in context_keys,
                f"{key!r} must not be inferred",
            )
        )

    for index, alternatives in enumerate(expected.get("required_message_term_groups", []), 1):
        checks.append(
            check(
                f"message_term_{index}",
                contains_any(message, alternatives),
                f"expected one of {alternatives!r} in the advice",
            )
        )

    for phrase in expected.get("forbidden_message_phrases", []):
        checks.append(
            check(
                f"forbidden_message_phrase_{phrase}",
                phrase.casefold() not in message.casefold(),
                f"boilerplate phrase {phrase!r} must be absent",
            )
        )

    max_questions = expected.get("max_question_marks")
    if isinstance(max_questions, int):
        checks.append(
            check(
                "question_limit",
                message.count("?") <= max_questions,
                f"expected at most {max_questions} question marks",
            )
        )

    return {
        "case_id": case_id,
        "passed": all(check["passed"] for check in checks),
        "raw_output": raw_output,
        "agent_meta": output.get("agent_meta"),
        "structural_checks": checks,
        "semantic_rubric": case["semantic_rubric"],
    }
