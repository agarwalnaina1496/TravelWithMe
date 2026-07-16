"""Shared helpers for agent behavior regression suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_case_manifest(path: Path, suite_name: str) -> list[dict[str, Any]]:
    """Load a reusable case manifest and validate its common structure."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{suite_name} manifest must contain a non-empty cases list")

    case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Every {suite_name} case needs a non-empty id")
        if case_id in case_ids:
            raise ValueError(f"Duplicate {suite_name} case id: {case_id}")
        case_ids.add(case_id)
        if not isinstance(case.get("request"), dict):
            raise ValueError(f"{case_id} is missing its request")
        if not isinstance(case.get("expected"), dict):
            raise ValueError(f"{case_id} is missing deterministic expectations")
        if not case.get("semantic_rubric"):
            raise ValueError(f"{case_id} is missing its semantic review rubric")
    return cases


def flatten_values(value: Any) -> list[str]:
    """Return every scalar value in a nested structure as text."""

    if isinstance(value, dict):
        flattened: list[str] = []
        for nested in value.values():
            flattened.extend(flatten_values(nested))
        return flattened
    if isinstance(value, list):
        flattened = []
        for nested in value:
            flattened.extend(flatten_values(nested))
        return flattened
    if value is None:
        return []
    return [str(value)]


def all_keys(value: Any) -> set[str]:
    """Return case-folded keys from a nested structure."""

    if isinstance(value, dict):
        keys = {str(key).casefold() for key in value}
        for nested in value.values():
            keys.update(all_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for nested in value:
            keys.update(all_keys(nested))
        return keys
    return set()


def contains_any(haystack: str, alternatives: list[str]) -> bool:
    """Return whether text contains at least one case-insensitive alternative."""

    folded = haystack.casefold()
    return any(alternative.casefold() in folded for alternative in alternatives)


def check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    """Build one normalized deterministic check result."""

    return {"id": check_id, "passed": passed, "detail": detail}
