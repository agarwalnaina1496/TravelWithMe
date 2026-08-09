"""CLI entrypoint for the Scout/Meridian/Guide/Atlas evaluation harness.

Run as a module from the repo root so the `twm` package and this package's
relative imports resolve: `python -m scripts.evaluation_harness.run`.
"""

import argparse
import asyncio
import sys

from twm.prompt_registry import load_prompt_release

from .fixtures import AGENTS, EvaluationCase, load_cases, load_fixtures, fixtures_for_case
from .parity import check_parity
from .pipeline import run_case
from .rubric import evaluate


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        choices=AGENTS,
        default=None,
        help="Restrict the run to one agent (default: all agents).",
    )
    return parser.parse_args(argv)


async def _run_case_report(case: EvaluationCase, fixtures) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    case_fixtures = fixtures_for_case(fixtures, case.agent, case.case_id)
    if not case_fixtures:
        lines.append(f"  [FAIL] {case.case_id}: no recorded fixture")
        return False, lines

    for fixture in case_fixtures:
        label = f"{case.case_id} ({fixture.execution_path})"
        try:
            execution = await run_case(case, fixture)
        except Exception as error:  # noqa: BLE001 - report every failure mode
            lines.append(f"  [FAIL] {label}: schema/pipeline error: {error}")
            ok = False
            continue

        try:
            evaluate(case, execution.response)
        except NotImplementedError as error:
            lines.append(f"  [WARN] {label}: {error}")
        except AssertionError as error:
            lines.append(f"  [FAIL] {label}: rubric violation: {error}")
            ok = False
            continue
        else:
            lines.append(f"  [PASS] {label}: schema valid, rubric satisfied")

        release = load_prompt_release(case.agent)
        if release.version != fixture.prompt_version:
            lines.append(
                f"  [WARN] {label}: fixture prompt_version {fixture.prompt_version!r} "
                f"differs from current {release.version!r}"
            )

    return ok, lines


async def _main_async(agent_filter: str | None) -> int:
    agents = (agent_filter,) if agent_filter else AGENTS
    fixtures = load_fixtures()
    overall_ok = True

    for agent in agents:
        print(f"\n== {agent} ==")
        for case in load_cases(agent):
            ok, lines = await _run_case_report(case, fixtures)
            overall_ok = overall_ok and ok
            for line in lines:
                print(line)

            parity_result = await check_parity(case, fixtures)
            if parity_result is not None:
                if parity_result.matches:
                    print(
                        f"  [PASS] {case.case_id}: parity holds across "
                        f"{parity_result.paths}"
                    )
                else:
                    overall_ok = False
                    print(
                        f"  [FAIL] {case.case_id}: parity diff across "
                        f"{parity_result.paths}: {parity_result.differences}"
                    )

    print("\n" + ("ALL CHECKS PASSED" if overall_ok else "CHECKS FAILED"))
    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    return asyncio.run(_main_async(args.agent))


if __name__ == "__main__":
    raise SystemExit(main())
