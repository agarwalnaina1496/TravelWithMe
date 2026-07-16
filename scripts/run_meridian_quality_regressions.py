"""Run reusable Meridian behavior cases through the normalized FastAPI endpoint."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from twm.quality.meridian_regression import evaluate_case, load_cases
from twm.quality.runner import run_suite


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "base_url",
        help="FastAPI base URL, for example http://localhost:8000",
    )
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run only this case id; repeat as needed",
    )
    args = parser.parse_args()

    cases = load_cases()
    if args.case_ids:
        requested = set(args.case_ids)
        cases = [case for case in cases if case["id"] in requested]
        missing = requested - {case["id"] for case in cases}
        if missing:
            parser.error(f"unknown case ids: {', '.join(sorted(missing))}")

    passed = run_suite(
        base_url=args.base_url,
        endpoint="/meridian",
        output_path=args.output,
        cases=cases,
        evaluator=evaluate_case,
        suite_name="TWM-44 Meridian quality",
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
