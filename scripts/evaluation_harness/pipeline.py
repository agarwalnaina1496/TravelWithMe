"""Run one evaluation case through the real AgentExecutionService pipeline."""

from typing import Any

from twm.services.agent_engine.contracts import AgentExecution
from twm.services.agent_engine.service import AgentExecutionService
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings

from .adapter import RecordedAdapter
from .fixtures import EvaluationCase, RecordedFixture


def build_service(raw_output: str, engine_name: str) -> AgentExecutionService:
    logger = TelemetryLogger(
        TelemetrySettings(True, "evaluation-harness", PayloadMode.FULL, 16_384),
        InMemorySink(),
    )
    return AgentExecutionService(
        RecordedAdapter(raw_output), logger, engine_name
    )


async def run_case(
    case: EvaluationCase, fixture: RecordedFixture
) -> AgentExecution:
    service = build_service(fixture.raw_output, fixture.execution_path)
    trip_state, message = _build_invocation_state(case)
    return await getattr(service, case.agent)(trip_state, message)


def _build_invocation_state(
    case: EvaluationCase,
) -> tuple[dict[str, Any], str | None]:
    if case.agent in {"scout", "meridian"}:
        return case.input["trip_state"], case.input.get("message")
    if case.agent == "guide":
        trip_state = {
            "trip_context": case.input["trip_context"],
            "guide_state": case.input.get("guide_state", {}),
        }
        trip_state["guide_event"] = case.input["event"]
        return trip_state, case.input.get("message")
    if case.agent == "atlas":
        trip_state = {
            "trip_context": case.input["trip_context"],
            "working_plan": case.input["working_plan"],
        }
        return trip_state, case.input.get("message")
    raise ValueError(f"Unknown agent: {case.agent}")
