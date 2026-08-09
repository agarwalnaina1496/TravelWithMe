"""Load evaluation cases and their recorded fixtures."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESOURCES_DIR = Path(__file__).resolve().parents[2] / "tests" / "resources"
FIXTURES_DIR = RESOURCES_DIR / "harness_fixtures"

CASE_FILES: dict[str, Path] = {
    "scout": RESOURCES_DIR / "scout_agent_cases.json",
    "meridian": RESOURCES_DIR / "meridian_agent_cases.json",
    "guide": RESOURCES_DIR / "guide_agent_cases.json",
    "atlas": RESOURCES_DIR / "atlas_agent_cases.json",
}

AGENTS = tuple(CASE_FILES)


@dataclass(frozen=True)
class EvaluationCase:
    agent: str
    case_id: str
    scenario: str
    input: dict[str, Any]
    invariants: dict[str, Any]


@dataclass(frozen=True)
class RecordedFixture:
    agent: str
    case_id: str
    execution_path: str
    prompt_version: str
    recorded_by: str
    raw_output: str


def load_cases(agent: str) -> list[EvaluationCase]:
    if agent not in CASE_FILES:
        raise ValueError(f"Unknown agent: {agent}")
    raw_cases = json.loads(CASE_FILES[agent].read_text(encoding="utf-8"))
    return [
        EvaluationCase(
            agent=agent,
            case_id=raw_case["id"],
            scenario=raw_case.get("scenario", ""),
            input=raw_case["input"],
            invariants=raw_case["invariants"],
        )
        for raw_case in raw_cases
    ]


def load_all_cases(agents: tuple[str, ...] = AGENTS) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for agent in agents:
        cases.extend(load_cases(agent))
    return cases


def load_fixtures() -> list[RecordedFixture]:
    fixtures: list[RecordedFixture] = []
    for fixture_file in sorted(FIXTURES_DIR.glob("*.json")):
        raw_fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
        fixtures.append(
            RecordedFixture(
                agent=raw_fixture["agent"],
                case_id=raw_fixture["case_id"],
                execution_path=raw_fixture["execution_path"],
                prompt_version=raw_fixture["prompt_version"],
                recorded_by=raw_fixture["recorded_by"],
                raw_output=raw_fixture["raw_output"],
            )
        )
    return fixtures


def fixtures_for_case(
    fixtures: list[RecordedFixture], agent: str, case_id: str
) -> list[RecordedFixture]:
    return [
        fixture
        for fixture in fixtures
        if fixture.agent == agent and fixture.case_id == case_id
    ]
