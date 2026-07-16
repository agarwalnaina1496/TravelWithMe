"""Reusable evaluation for recorded Meridian behavior-regression runs."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..schemas.meridian import MeridianResponse
from .common import all_keys, check, contains_any, flatten_values, load_case_manifest


DEFAULT_CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "meridian_quality_cases.json"
)

_NUMBER = r"(\d+(?:\.\d+)?)"
_LEG_METRICS = re.compile(
    rf"{_NUMBER}\s*km\b.*?{_NUMBER}\s*hours?\b",
    re.IGNORECASE,
)
_TOTAL_METRICS = re.compile(
    rf"total driving:\s*(?:about\s*)?{_NUMBER}\s*km\b.*?{_NUMBER}\s*hours?\b",
    re.IGNORECASE,
)
_AVERAGE_METRICS = re.compile(
    rf"average daily driving:\s*(?:about\s*)?{_NUMBER}\s*km\b.*?"
    rf"{_NUMBER}\s*hours?\b.*?{_NUMBER}\s*driving days?\b",
    re.IGNORECASE,
)


def load_cases(path: Path = DEFAULT_CASES_FILE) -> list[dict[str, Any]]:
    """Load and validate the reusable Meridian behavior-case manifest."""

    return load_case_manifest(path, "Meridian quality")


def _term_group_checks(
    prefix: str,
    text: str,
    groups: list[list[str]],
) -> list[dict[str, Any]]:
    return [
        check(
            f"{prefix}_{index}",
            contains_any(text, alternatives),
            f"expected one of {alternatives!r}",
        )
        for index, alternatives in enumerate(groups, 1)
    ]


def _sections_by_type(option: dict[str, Any], section_type: str) -> list[dict[str, Any]]:
    sections = option.get("sections")
    if not isinstance(sections, list):
        return []
    return [
        section
        for section in sections
        if isinstance(section, dict) and section.get("type") == section_type
    ]


def _route_math_checks(
    option: dict[str, Any],
    option_index: int,
    expectation: dict[str, Any],
) -> list[dict[str, Any]]:
    prefix = f"option_{option_index}_route"
    checks: list[dict[str, Any]] = []

    stops_sections = _sections_by_type(option, "stops")
    stops = stops_sections[0].get("stops", []) if stops_sections else []
    nights = [
        stop.get("nights")
        for stop in stops
        if isinstance(stop, dict) and isinstance(stop.get("nights"), int)
    ]
    total_nights = sum(nights)
    complete_nights = bool(stops) and len(nights) == len(stops)
    min_nights = expectation["min_nights"]
    max_nights = expectation["max_nights"]
    checks.append(
        check(
            f"{prefix}_nights",
            complete_nights and min_nights <= total_nights <= max_nights,
            f"expected {min_nights}-{max_nights} allocated nights, got {total_nights}",
        )
    )

    travel_sections = _sections_by_type(option, "internal_travel")
    legs = travel_sections[0].get("legs", []) if travel_sections else []
    leg_metrics: list[tuple[float, float]] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        match = _LEG_METRICS.search(str(leg.get("duration", "")))
        if match:
            leg_metrics.append((float(match.group(1)), float(match.group(2))))
    checks.append(
        check(
            f"{prefix}_leg_metrics",
            bool(legs) and len(leg_metrics) == len(legs),
            "every internal-travel leg must state distance and driving time",
        )
    )

    route_text = "\n".join(
        flatten_values(_sections_by_type(option, "route"))
    )
    total_match = _TOTAL_METRICS.search(route_text)
    average_match = _AVERAGE_METRICS.search(route_text)
    checks.append(
        check(
            f"{prefix}_total_present",
            total_match is not None,
            "route section must state total driving distance and time",
        )
    )
    checks.append(
        check(
            f"{prefix}_average_present",
            average_match is not None,
            "route section must state average daily driving and driving days",
        )
    )

    if leg_metrics and total_match:
        stated_km = float(total_match.group(1))
        stated_hours = float(total_match.group(2))
        leg_km = sum(item[0] for item in leg_metrics)
        leg_hours = sum(item[1] for item in leg_metrics)
        checks.extend(
            [
                check(
                    f"{prefix}_distance_total",
                    math.isclose(leg_km, stated_km, rel_tol=0.1, abs_tol=20),
                    f"leg distance {leg_km:g} km versus stated total {stated_km:g} km",
                ),
                check(
                    f"{prefix}_time_total",
                    math.isclose(leg_hours, stated_hours, rel_tol=0.1, abs_tol=1),
                    f"leg time {leg_hours:g} hours versus stated total {stated_hours:g} hours",
                ),
            ]
        )

    if total_match and average_match:
        stated_km = float(total_match.group(1))
        stated_hours = float(total_match.group(2))
        average_km = float(average_match.group(1))
        average_hours = float(average_match.group(2))
        driving_days = float(average_match.group(3))
        max_days = expectation["max_days"]
        checks.extend(
            [
                check(
                    f"{prefix}_driving_days",
                    1 <= driving_days <= max_days,
                    f"expected no more than {max_days} driving days, got {driving_days:g}",
                ),
                check(
                    f"{prefix}_daily_distance",
                    math.isclose(
                        average_km * driving_days,
                        stated_km,
                        rel_tol=0.1,
                        abs_tol=20,
                    ),
                    "average daily distance must reconcile with route total",
                ),
                check(
                    f"{prefix}_daily_time",
                    math.isclose(
                        average_hours * driving_days,
                        stated_hours,
                        rel_tol=0.1,
                        abs_tol=1,
                    ),
                    "average daily time must reconcile with route total",
                ),
            ]
        )

    return checks


def evaluate_case(
    case: dict[str, Any], raw_output: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate deterministic checks and preserve the manual semantic rubric."""

    case_id = case["id"]
    try:
        response = MeridianResponse.model_validate(raw_output)
    except ValidationError as exc:
        agent_meta = raw_output.get("agent_meta") if isinstance(raw_output, dict) else None
        return {
            "case_id": case_id,
            "passed": False,
            "raw_output": raw_output,
            "agent_meta": agent_meta,
            "structural_checks": [check("canonical_response", False, str(exc))],
            "semantic_rubric": case["semantic_rubric"],
        }

    output = response.model_dump(mode="json")
    expected = case["expected"]
    message = output.get("message") or ""
    options = output["options"]
    output_text = "\n".join(flatten_values(output))
    conversation_context = (
        output["state_delta"]
        .get("matcher_state", {})
        .get("conversation_context", {})
    )
    last_message = conversation_context.get("last_meridian_message")
    awaiting = conversation_context.get("awaiting")
    checks = [
        check("canonical_response", True, "Response matches MeridianResponse"),
        check(
            "agent_provenance",
            output["agent_meta"]["agent"] == "meridian",
            f"expected 'meridian', got {output['agent_meta']['agent']!r}",
        ),
        check(
            "status",
            output["status"] in expected["allowed_statuses"],
            f"expected one of {expected['allowed_statuses']!r}, got {output['status']!r}",
        ),
        check(
            "last_message",
            last_message == message,
            "last_meridian_message must match the visible message",
        ),
    ]

    if output["status"] == "NEEDS_CLARIFICATION":
        checks.append(
            check(
                "awaiting_present",
                isinstance(awaiting, str) and bool(awaiting.strip()),
                "clarification must name the awaited answer",
            )
        )
    else:
        checks.append(
            check(
                "awaiting_cleared",
                awaiting is None,
                "terminal outcomes must clear awaiting",
            )
        )

    options_mode = expected.get("options_mode")
    if options_mode == "empty":
        checks.append(check("options_empty", not options, "clarification has no options"))
    elif options_mode == "ranked":
        min_options = expected.get("min_options", 1)
        max_options = expected.get("max_options", 3)
        checks.append(
            check(
                "option_count",
                min_options <= len(options) <= max_options,
                f"expected {min_options}-{max_options} options, got {len(options)}",
            )
        )

    question_marks = expected.get("question_marks")
    if isinstance(question_marks, int):
        checks.append(
            check(
                "question_count",
                message.count("?") == question_marks,
                f"expected {question_marks} question marks",
            )
        )

    awaiting_groups = expected.get("awaiting_value_groups", [])
    if awaiting_groups:
        checks.extend(
            _term_group_checks("awaiting", str(awaiting or ""), awaiting_groups)
        )

    context = output["state_delta"]["trip_context"]
    context_text = "\n".join(flatten_values(context))
    checks.extend(
        _term_group_checks(
            "context_value",
            context_text,
            expected.get("required_context_value_groups", []),
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

    checks.extend(
        _term_group_checks(
            "output_value",
            output_text,
            expected.get("required_output_value_groups", []),
        )
    )
    for phrase in expected.get("forbidden_output_phrases", []):
        checks.append(
            check(
                f"forbidden_output_phrase_{phrase}",
                phrase.casefold() not in output_text.casefold(),
                f"unsupported phrase {phrase!r} must be absent",
            )
        )

    required_fields = expected.get("required_option_fields", [])
    per_option_groups = expected.get("per_option_required_value_groups", [])
    require_tradeoffs = expected.get("require_tradeoffs", False)
    route_math = expected.get("route_math")
    for index, option in enumerate(options, 1):
        option_text = "\n".join(flatten_values(option))
        for field in required_fields:
            checks.append(
                check(
                    f"option_{index}_field_{field}",
                    field in option and option[field] not in (None, "", [], {}),
                    f"option must include non-empty {field!r}",
                )
            )
        checks.extend(
            _term_group_checks(
                f"option_{index}_value",
                option_text,
                per_option_groups,
            )
        )
        if require_tradeoffs:
            decision_summary = option.get("decision_summary")
            tradeoffs = (
                decision_summary.get("tradeoffs", [])
                if isinstance(decision_summary, dict)
                else []
            )
            checks.append(
                check(
                    f"option_{index}_tradeoffs",
                    isinstance(tradeoffs, list) and bool(tradeoffs),
                    "option must disclose traveler-specific tradeoffs",
                )
            )
        if route_math and option.get("type") == "circuit":
            checks.extend(_route_math_checks(option, index, route_math))

    if expected.get("circuits_only") and options:
        checks.append(
            check(
                "circuits_only",
                all(option.get("type") == "circuit" for option in options),
                "every recommendation must be a circuit",
            )
        )

    return {
        "case_id": case_id,
        "passed": all(item["passed"] for item in checks),
        "raw_output": raw_output,
        "agent_meta": output.get("agent_meta"),
        "structural_checks": checks,
        "semantic_rubric": case["semantic_rubric"],
    }
