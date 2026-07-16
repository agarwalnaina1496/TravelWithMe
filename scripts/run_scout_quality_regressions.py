"""Run reusable Scout behavior cases through the normalized FastAPI endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from twm.quality.scout_regression import evaluate_case, load_cases


def run_case(client: httpx.Client, case: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post("/scout", json=case["request"])
        response.raise_for_status()
        return evaluate_case(case, response.json())
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="FastAPI base URL, for example http://localhost:8000")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this case id; repeat as needed")
    args = parser.parse_args()

    cases = load_cases()
    if args.case_ids:
        requested = set(args.case_ids)
        cases = [case for case in cases if case["id"] in requested]
        missing = requested - {case["id"] for case in cases}
        if missing:
            parser.error(f"unknown case ids: {', '.join(sorted(missing))}")

    with httpx.Client(base_url=args.base_url, timeout=90.0) as client:
        results = [run_case(client, case) for case in cases]

    report = {
        "suite": "twm-43-scout-quality",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "structural_passed": all(result["passed"] for result in results),
        "semantic_review_required": True,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    passed = sum(result["passed"] for result in results)
    print(f"Scout structural checks: {passed}/{len(results)} cases passed")
    print(f"Raw output, prompt provenance, and semantic rubrics: {args.output}")
    return 0 if report["structural_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
