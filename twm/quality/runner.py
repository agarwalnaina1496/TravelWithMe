"""HTTP execution and reporting shared by agent regression suites."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx


CaseEvaluator = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def run_case(
    client: httpx.Client,
    endpoint: str,
    case: dict[str, Any],
    evaluator: CaseEvaluator,
) -> dict[str, Any]:
    """Post one case to FastAPI and evaluate its normalized response."""

    try:
        response = client.post(endpoint, json=case["request"])
        response.raise_for_status()
        return evaluator(case, response.json())
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        return {
            "case_id": case["id"],
            "passed": False,
            "raw_output": None,
            "agent_meta": None,
            "structural_checks": [
                {"id": "request", "passed": False, "detail": str(exc)}
            ],
            "semantic_rubric": case["semantic_rubric"],
        }


def run_suite(
    *,
    base_url: str,
    endpoint: str,
    output_path: Path,
    cases: list[dict[str, Any]],
    evaluator: CaseEvaluator,
    suite_name: str,
) -> bool:
    """Run a suite and write its reviewable JSON report."""

    with httpx.Client(base_url=base_url, timeout=90.0) as client:
        results = [run_case(client, endpoint, case, evaluator) for case in cases]

    report = {
        "suite": suite_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "structural_passed": all(result["passed"] for result in results),
        "semantic_review_required": True,
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    passed = sum(result["passed"] for result in results)
    print(f"{suite_name} structural checks: {passed}/{len(results)} cases passed")
    print(f"Raw output, prompt provenance, and semantic rubrics: {output_path}")
    return report["structural_passed"]
