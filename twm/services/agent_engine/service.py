"""Common Scout and Meridian execution, parsing, validation, and repair."""

import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ...prompts import PromptRelease, load_prompt_release
from ...schemas import MeridianAgentOutput, ScoutAgentOutput
from ...security import frame_untrusted_payload
from ...telemetry import TelemetryLogger
from .contracts import (
    AgentAdapter,
    AgentExecution,
    AgentInvocation,
    AgentName,
    AgentOutputError,
)


logger = logging.getLogger("uvicorn.error")

OUTPUT_CONTRACT_INSTRUCTION = (
    "Return only one JSON object matching the JSON Schema below. "
    "Do not wrap it in Markdown or add explanatory text.\n"
)
REGENERATION_SYSTEM_INSTRUCTION = (
    "The previous completion failed the required output contract. Repair that "
    "response by generating a fresh answer from the original traveler request. "
    "Do not invent generic replacement content. Return only the regenerated "
    "JSON object. Sanitized validation failures from the previous attempt: "
)
REDACTED_LOCATION = "<redacted>"


@dataclass(frozen=True)
class AgentDefinition:
    output_model: type[BaseModel]


class _OutputValidationFailure(ValueError):
    def __init__(self, failures: list[dict[str, Any]]) -> None:
        super().__init__("model output failed validation")
        self.failures = failures


AGENT_DEFINITIONS: dict[AgentName, AgentDefinition] = {
    "scout": AgentDefinition(ScoutAgentOutput),
    "meridian": AgentDefinition(MeridianAgentOutput),
}


class AgentExecutionService:
    """Run both agents through one engine-independent application pipeline."""

    def __init__(self, adapter: AgentAdapter, telemetry_logger: TelemetryLogger | None = None) -> None:
        self._adapter = adapter
        self._telemetry_logger = telemetry_logger

    def _agent_event_fields(
        self,
        agent: AgentName,
        attempt: int,
        prompt_version: str,
        status: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine_name = self._adapter.engine_name
        if not isinstance(engine_name, str):
            engine_name = None
        fields: dict[str, Any] = {
            "agent": agent,
            "engine": engine_name,
            "prompt_version": prompt_version,
            "attempt": attempt,
        }
        endpoint_callable = self._adapter.endpoint
        if not inspect.iscoroutinefunction(endpoint_callable):
            endpoint = endpoint_callable(agent)
        else:
            endpoint = None
        if endpoint is not None:
            fields["endpoint"] = endpoint
        if status is not None:
            fields["status"] = status
        if extra_fields:
            fields.update(extra_fields)
        return fields

    def _emit(
        self,
        name: str,
        *,
        level: str = "INFO",
        source: str = "application",
        fields: dict[str, Any] | None = None,
        payload: Any = None,
    ) -> None:
        if self._telemetry_logger is None:
            return
        self._telemetry_logger.event(
            name,
            level=level,
            source=source,
            fields=fields,
            payload=payload,
        )

    async def scout(
        self, trip_state: dict[str, Any], message: str | None
    ) -> AgentExecution:
        return await self._execute("scout", trip_state, message)

    async def meridian(
        self, trip_state: dict[str, Any], message: str | None
    ) -> AgentExecution:
        return await self._execute("meridian", trip_state, message)

    async def _execute(
        self,
        agent: AgentName,
        trip_state: dict[str, Any],
        message: str | None,
    ) -> AgentExecution:
        release = load_prompt_release(agent)
        definition = AGENT_DEFINITIONS[agent]
        invocation = _build_invocation(
            release, definition.output_model, trip_state, message
        )
        self._emit(
            "be.agent.input.prepared",
            source="agent",
            fields=self._agent_event_fields(agent, 1, release.version, "prepared"),
            payload={
                "system_prompt": invocation.system_prompt,
                "user_prompt": invocation.user_prompt,
            },
        )
        raw_output = await self._invoke(agent, invocation, attempt=1, prompt_version=release.version)

        try:
            response = _parse_and_validate(raw_output, definition)
        except _OutputValidationFailure as first_failure:
            self._emit(
                "be.agent.output.validation_failed",
                level="WARNING",
                source="agent",
                fields=self._agent_event_fields(agent, 1, release.version, "invalid"),
                payload={"failures": first_failure.failures},
            )
            self._emit(
                "be.agent.repair.started",
                source="agent",
                fields=self._agent_event_fields(agent, 2, release.version, "repair_started"),
                payload={"previous_failures": first_failure.failures},
            )
            regenerated_output = await self._invoke(
                agent,
                _build_regeneration_invocation(invocation, first_failure.failures),
                attempt=2,
                prompt_version=release.version,
            )
            try:
                response = _parse_and_validate(regenerated_output, definition)
            except _OutputValidationFailure as final_failure:
                self._emit(
                    "be.agent.output.validation_failed",
                    level="WARNING",
                    source="agent",
                    fields=self._agent_event_fields(agent, 2, release.version, "invalid"),
                    payload={"failures": final_failure.failures},
                )
                logger.warning(
                    "%s output remained invalid after one regeneration: %s",
                    agent,
                    final_failure.failures,
                )
                raise AgentOutputError(agent, final_failure.failures) from None

        self._emit(
            "be.agent.output.validated",
            source="agent",
            fields=self._agent_event_fields(agent, 2 if "previous_failures" in locals() else 1, release.version, "valid"),
            payload=response,
        )

        return AgentExecution(response=response, prompt_release=release)

    async def _invoke(
        self,
        agent: AgentName,
        invocation: AgentInvocation,
        attempt: int,
        prompt_version: str,
    ) -> str:
        started_at = time.perf_counter()
        self._emit(
            "be.agent.invocation.started",
            source="agent",
            fields=self._agent_event_fields(agent, attempt, prompt_version, "started"),
        )
        try:
            result = await self._adapter.invoke(agent, invocation)
        except Exception as error:
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            # Emit telemetry event for invocation failure
            self._emit(
                "be.agent.invocation.failed",
                level="WARNING",
                source="agent",
                fields=self._agent_event_fields(
                    agent,
                    attempt,
                    prompt_version,
                    "failed",
                    {
                        "duration_ms": duration_ms,
                        "error_type": type(error).__name__,
                    },
                ),
            )
            logger.warning(
                "Agent invocation failed: agent=%s attempt=%s duration_ms=%s "
                "error_type=%s",
                agent,
                attempt,
                duration_ms,
                type(error).__name__,
            )
            raise
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        metadata = result.metadata
        self._emit(
            "be.agent.raw_response.received",
            source="agent",
            fields=self._agent_event_fields(
                agent,
                attempt,
                prompt_version,
                "success",
                {
                    "duration_ms": duration_ms,
                    "response_size": len(result.raw_output),
                    **metadata,
                },
            ),
            payload={
                "raw_output": result.raw_output,
                "metadata": metadata,
            },
        )
        logger.info(
            "Agent invocation completed: agent=%s attempt=%s duration_ms=%s "
            "raw_output_chars=%s finish_reason=%s input_tokens=%s "
            "output_tokens=%s reasoning_tokens=%s total_tokens=%s "
            "queue_time_ms=%s model_time_ms=%s provider_total_time_ms=%s "
            "provider_attempts=%s",
            agent,
            attempt,
            duration_ms,
            len(result.raw_output),
            metadata.get("finish_reason"),
            metadata.get("input_tokens"),
            metadata.get("output_tokens"),
            metadata.get("reasoning_tokens"),
            metadata.get("total_tokens"),
            metadata.get("queue_time_ms"),
            metadata.get("model_time_ms"),
            metadata.get("provider_total_time_ms"),
            metadata.get("provider_attempts"),
        )
        return result.raw_output


def _build_invocation(
    release: PromptRelease,
    output_model: type[BaseModel],
    trip_state: dict[str, Any],
    message: str | None,
) -> AgentInvocation:
    schema = json.dumps(
        output_model.model_json_schema(), ensure_ascii=False, separators=(",", ":")
    )
    return AgentInvocation(
        system_prompt=(
            f"{release.content}\n\n{OUTPUT_CONTRACT_INSTRUCTION}{schema}"
        ),
        user_prompt=frame_untrusted_payload(trip_state, message),
    )


def _build_regeneration_invocation(
    original: AgentInvocation,
    failures: list[dict[str, Any]],
) -> AgentInvocation:
    failure_summary = json.dumps(
        {"validation_failures": failures}, ensure_ascii=False, separators=(",", ":")
    )
    return AgentInvocation(
        system_prompt=(
            f"{original.system_prompt}\n\n{REGENERATION_SYSTEM_INSTRUCTION}"
            f"{failure_summary}"
        ),
        user_prompt=original.user_prompt,
    )


def _parse_and_validate(
    raw_output: str, definition: AgentDefinition
) -> dict[str, Any]:
    try:
        decoded = json.loads(raw_output)
    except (TypeError, json.JSONDecodeError):
        raise _OutputValidationFailure(
            [{"type": "json_invalid", "loc": []}]
        ) from None

    try:
        parsed = definition.output_model.model_validate(decoded)
        return parsed.model_dump(mode="json", exclude_none=True)
    except ValidationError as error:
        failures = _sanitized_validation_failures(error, definition.output_model)
        raise _OutputValidationFailure(failures) from None


def _log_validation_failure(
    agent: AgentName, attempt: int, failures: list[dict[str, Any]]
) -> None:
    logger.warning(
        "Agent output validation failed: agent=%s attempt=%s failure_types=%s",
        agent,
        attempt,
        [failure["type"] for failure in failures],
    )


def _sanitized_validation_failures(
    error: ValidationError, output_model: type[BaseModel]
) -> list[dict[str, Any]]:
    known_fields = _schema_property_names(output_model.model_json_schema())
    return [
        {
            "type": item["type"],
            "loc": [
                component
                if isinstance(component, int)
                or (
                    isinstance(component, str)
                    and component in known_fields
                )
                else REDACTED_LOCATION
                for component in item["loc"]
            ],
        }
        for item in error.errors(include_input=False)
    ]


def _schema_property_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for child in value.values():
            names.update(_schema_property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_schema_property_names(child))
    return names
