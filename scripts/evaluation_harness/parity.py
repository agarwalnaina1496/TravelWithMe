"""Diff normalized responses across execution paths for the same case."""

from dataclasses import dataclass
from typing import Any

from .fixtures import EvaluationCase, RecordedFixture, fixtures_for_case
from .pipeline import run_case


@dataclass(frozen=True)
class ParityResult:
    agent: str
    case_id: str
    paths: tuple[str, ...]
    differences: dict[str, tuple[Any, Any]]

    @property
    def matches(self) -> bool:
        return not self.differences


def _diff(left: dict[str, Any], right: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    keys = set(left) | set(right)
    return {
        key: (left.get(key), right.get(key))
        for key in sorted(keys)
        if key != "agent_meta" and left.get(key) != right.get(key)
    }


async def check_parity(
    case: EvaluationCase, fixtures: list[RecordedFixture]
) -> ParityResult | None:
    case_fixtures = fixtures_for_case(fixtures, case.agent, case.case_id)
    if len(case_fixtures) < 2:
        return None
    responses: dict[str, dict[str, Any]] = {}
    for fixture in case_fixtures:
        execution = await run_case(case, fixture)
        responses[fixture.execution_path] = execution.response
    paths = sorted(responses)
    differences = _diff(responses[paths[0]], responses[paths[1]])
    return ParityResult(
        agent=case.agent, case_id=case.case_id, paths=tuple(paths), differences=differences
    )
