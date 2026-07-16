"""Reusable evaluation for recorded Scout behavior-regression runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..schemas.scout import ScoutResponse


DEFAULT_CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "scout_quality_cases.json"
)


def load_cases(path: Path = DEFAULT_CASES_FILE) -> list[dict[str, Any]]:
    """Load and validate the reusable Scout behavior-case manifest."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Scout quality manifest must contain a non-empty cases list")

    case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("Every Scout quality case needs a non-empty id")
        if case_id in case_ids:
            raise ValueError(f"Duplicate Scout quality case id: {case_id}")
        case_ids.add(case_id)
        if not isinstance(case.get("request"), dict):
            raise ValueError(f"{case_id} is missing its request")
        if not isinstance(case.get("expected"), dict):
            raise ValueError(f"{case_id} is missing deterministic expectations")
        if not case.get("semantic_rubric"):
            raise ValueError(f"{case_id} is missing its semantic review rubric")
    return cases


def _flatten_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        flattened: list[str] = []
        for nested in value.values():
            flattened.extend(_flatten_values(nested))
        return flattened
    if isinstance(value, list):
        flattened = []
        for nested in value:
            flattened.extend(_flatten_values(nested))
        return flattened
    if value is None:
        return []
    return [str(value)]


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key).casefold() for key in value}
        for nested in value.values():
            keys.update(_all_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for nested in value:
            keys.update(_all_keys(nested))
        return keys
    return set()


def _contains_any(haystack: str, alternatives: list[str]) -> bool:
    folded = haystack.casefold()
    return any(alternative.casefold() in folded for alternative in alternatives)


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": passed, "detail": detail}


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
                _check("canonical_response", False, str(exc))
            ],
            "semantic_rubric": case["semantic_rubric"],
        }

    output = response.model_dump(mode="json")
    expected = case["expected"]
    context = output["state_delta"]["trip_context"]
    context_text = "\n".join(_flatten_values(context))
    message = output.get("message") or ""
    checks = [
        _check("canonical_response", True, "Response matches ScoutResponse"),
        _check(
            "agent_provenance",
            output["agent_meta"]["agent"] == "scout",
            f"expected 'scout', got {output['agent_meta']['agent']!r}",
        ),
        _check(
            "intent",
            output.get("intent") == expected.get("intent"),
            f"expected {expected.get('intent')!r}, got {output.get('intent')!r}",
        ),
    ]

    message_mode = expected.get("message_mode")
    if message_mode == "empty":
        checks.append(_check("message_empty", not message.strip(), "Matcher/Planner handoff message is empty"))
    elif message_mode == "non_empty":
        checks.append(_check("message_non_empty", bool(message.strip()), "Visible Scout response is present"))

    for index, alternatives in enumerate(expected.get("required_context_value_groups", []), 1):
        checks.append(
            _check(
                f"context_value_{index}",
                _contains_any(context_text, alternatives),
                f"expected one of {alternatives!r} in extracted context",
            )
        )

    context_keys = _all_keys(context)
    for key in expected.get("forbidden_context_keys", []):
        checks.append(
            _check(
                f"forbidden_context_key_{key}",
                key.casefold() not in context_keys,
                f"{key!r} must not be inferred",
            )
        )

    for index, alternatives in enumerate(expected.get("required_message_term_groups", []), 1):
        checks.append(
            _check(
                f"message_term_{index}",
                _contains_any(message, alternatives),
                f"expected one of {alternatives!r} in the advice",
            )
        )

    for phrase in expected.get("forbidden_message_phrases", []):
        checks.append(
            _check(
                f"forbidden_message_phrase_{phrase}",
                phrase.casefold() not in message.casefold(),
                f"boilerplate phrase {phrase!r} must be absent",
            )
        )

    max_questions = expected.get("max_question_marks")
    if isinstance(max_questions, int):
        checks.append(
            _check(
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
