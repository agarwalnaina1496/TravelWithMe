"""Run the recorded evaluation harness cases through the real agent pipeline."""

import asyncio

import pytest

from scripts.evaluation_harness.fixtures import (
    AGENTS,
    load_all_cases,
    load_fixtures,
    fixtures_for_case,
)
from scripts.evaluation_harness.parity import check_parity
from scripts.evaluation_harness.pipeline import run_case
from scripts.evaluation_harness.rubric import KNOWN_UNIMPLEMENTED_INVARIANTS, evaluate, missing_checks

ALL_CASES = load_all_cases()
ALL_FIXTURES = load_fixtures()


def _case_fixture_pairs():
    pairs = []
    for case in ALL_CASES:
        for fixture in fixtures_for_case(ALL_FIXTURES, case.agent, case.case_id):
            pairs.append(pytest.param(case, fixture, id=f"{case.agent}-{case.case_id}-{fixture.execution_path}"))
    return pairs


@pytest.mark.parametrize("case,fixture", _case_fixture_pairs())
def test_recorded_fixture_satisfies_schema_and_rubric(case, fixture) -> None:
    execution = asyncio.run(run_case(case, fixture))

    try:
        evaluate(case, execution.response)
    except NotImplementedError:
        # Only a documented, allow-listed gap may pass silently here; an
        # undocumented one means a new case added an invariant key with no
        # rubric check, which must fail loudly instead of being swallowed.
        undocumented = missing_checks([case]) - KNOWN_UNIMPLEMENTED_INVARIANTS
        assert not undocumented, (
            f"{case.agent}/{case.case_id}: undocumented missing rubric check(s) "
            f"{undocumented} — add a check or add to KNOWN_UNIMPLEMENTED_INVARIANTS"
        )


def test_missing_rubric_checks_match_known_gaps() -> None:
    assert missing_checks(ALL_CASES) == set(KNOWN_UNIMPLEMENTED_INVARIANTS)


def test_every_case_has_at_least_one_fixture() -> None:
    missing = [
        f"{case.agent}/{case.case_id}"
        for case in ALL_CASES
        if not fixtures_for_case(ALL_FIXTURES, case.agent, case.case_id)
    ]
    assert not missing, f"cases missing recorded fixtures: {missing}"


@pytest.mark.parametrize("agent", AGENTS)
def test_representative_direct_n8n_parity(agent) -> None:
    for case in load_all_cases((agent,)):
        result = asyncio.run(check_parity(case, ALL_FIXTURES))
        if result is not None:
            assert result.matches, (
                f"{agent}/{case.case_id} parity diff across {result.paths}: "
                f"{result.differences}"
            )
