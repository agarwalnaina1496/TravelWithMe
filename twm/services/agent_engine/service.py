"""Common agent execution, parsing, and validation."""

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ...prompt_registry import PromptRelease, load_prompt_release
from ...schemas import (
    AtlasAgentOutput,
    GuideAgentOutput,
    MeridianAgentOutput,
    ScoutAgentOutput,
)
from ...trust_boundary import frame_untrusted_payload
from ...telemetry import TelemetryLogger
from ...telemetry.sanitization import redact_error_detail
from .contracts import (
    AgentAdapter,
    AgentAdapterError,
    AgentExecution,
    AgentInvocation,
    AgentInvocationResult,
    AgentName,
    AgentOutputError,
    GenerationConfig,
)
from .shape_normalization import normalize_agent_output

OUTPUT_CONTRACT_INSTRUCTION = (
    "\n\nOUTPUT CONTRACT:\n"
    "Return exactly one complete JSON object and no markdown, commentary, or "
    "code fences. The object must match this JSON Schema:\n"
)
REDACTED_LOCATION = "<redacted>"
# TWM-234: one fresh retry (2 attempts total) when the model's own output
# fails our schema/contract validation -- never infinite, and never a
# corrective retry (no validation-error feedback appended to the prompt),
# just a brand new generation call against the exact same invocation.
MAX_OUTPUT_VALIDATION_ATTEMPTS = 2


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
    "guide": AgentDefinition(GuideAgentOutput),
    "atlas": AgentDefinition(AtlasAgentOutput),
}


class AgentExecutionService:
    """Run both agents through one engine-independent application pipeline."""

    def __init__(
        self,
        adapter: AgentAdapter,
        logger: TelemetryLogger,
        engine_name: str,
        generation: GenerationConfig | None = None,
    ) -> None:
        self._adapter = adapter
        self._logger = logger
        self._engine_name = engine_name
        self._generation = generation or GenerationConfig()

    async def scout(
        self, trip_state: dict[str, Any], message: str | None
    ) -> AgentExecution:
        return await self._execute("scout", trip_state, message)

    async def meridian(
        self, trip_state: dict[str, Any], message: str | None
    ) -> AgentExecution:
        return await self._execute("meridian", trip_state, message)

    async def guide(
        self, trip_state: dict[str, Any], message: str | None
    ) -> AgentExecution:
        return await self._execute("guide", trip_state, message)

    async def atlas(
        self, trip_state: dict[str, Any], message: str | None = None
    ) -> AgentExecution:
        return await self._execute("atlas", trip_state, message)

    async def _execute(
        self,
        agent: AgentName,
        trip_state: dict[str, Any],
        message: str | None,
    ) -> AgentExecution:
        release = load_prompt_release(agent)
        definition = AGENT_DEFINITIONS[agent]
        invocation = _build_invocation(
            release,
            definition.output_model,
            trip_state,
            message,
            self._generation,
        )

        last_failure: _OutputValidationFailure | None = None
        for attempt in range(1, MAX_OUTPUT_VALIDATION_ATTEMPTS + 1):
            invocation_result = await self._invoke(
                agent,
                invocation,
                attempt=attempt,
                prompt_version=release.version,
                traveler_message=message,
            )
            try:
                response, applied_normalizations = _parse_and_validate(
                    agent, invocation_result.raw_output, definition
                )
            except _OutputValidationFailure as failure:
                last_failure = failure
                will_retry = attempt < MAX_OUTPUT_VALIDATION_ATTEMPTS
                self._log_output_validation_failure(
                    agent, invocation_result.raw_output, failure, attempt, will_retry
                )
                continue

            if applied_normalizations:
                self._logger.info(
                    f"{agent.capitalize()} response from {self._engine_name} "
                    f"was reshaped by default normalization before validation: "
                    f"{', '.join(applied_normalizations)}.",
                    event="be.agent.output.normalized",
                    source="agent_engine",
                    agent=agent,
                    engine=self._engine_name,
                    component="fastapi",
                    operation=f"{agent}.response.validate",
                    attempt=attempt,
                    status="normalized",
                    normalizations_applied=applied_normalizations,
                )

            self._logger.info(
                f"{agent.capitalize()} agent response received from "
                f"{_display_engine_name(self._engine_name)}. Response - "
                f"{self._logger.format_json(response)}",
                event="be.agent.response.received",
                source="agent_engine",
                fields=invocation_result.metadata,
                response=response,
            )
            return AgentExecution(response=response, prompt_release=release)

        raise AgentOutputError(agent, last_failure.failures) from None

    def _log_output_validation_failure(
        self,
        agent: AgentName,
        raw_output: str,
        failure: _OutputValidationFailure,
        attempt: int,
        will_retry: bool,
    ) -> None:
        # TWM-234: a single malformed/off-schema generation is common LLM
        # noise, not necessarily a broken prompt -- one fresh retry (a brand
        # new generation call, no corrective feedback appended) resolves
        # most of them without ever surfacing a failure to the traveler.
        # Only the final attempt logs at error severity and raises.
        log = self._logger.warning if will_retry else self._logger.error
        outcome = "retrying with a fresh attempt" if will_retry else "giving up"
        log(
            f"FastAPI rejected {agent.capitalize()} response from "
            f"{self._engine_name}. Detail - AgentOutputValidationError: "
            f"{len(failure.failures)} contract violation(s), {outcome}. "
            f"Response - {self._logger.format_json(raw_output)}",
            event="be.agent.output.invalid",
            source="agent_engine",
            agent=agent,
            engine=self._engine_name,
            component="fastapi",
            operation=f"{agent}.response.validate",
            failure_stage="agent_output_validation",
            error_type="AgentOutputValidationError",
            attempt=attempt,
            status="retrying" if will_retry else "failed",
            raw_output_chars=len(raw_output),
            validation_failures=failure.failures,
            response=raw_output,
        )

    async def _invoke(
        self,
        agent: AgentName,
        invocation: AgentInvocation,
        attempt: int,
        prompt_version: str,
        traveler_message: str | None,
    ) -> AgentInvocationResult:
        common_fields = {
            "agent": agent,
            "engine": self._engine_name,
            "attempt": attempt,
            "prompt_version": prompt_version,
        }
        self._logger.info(
            f"{agent.capitalize()} agent called via "
            f"{_display_engine_name(self._engine_name)} with message "
            f"{_quoted_message(traveler_message)}",
            event="be.agent.invocation.started",
            source="agent_engine",
            fields=common_fields,
            payload={
                "user_prompt": invocation.user_prompt,
            },
        )
        started_at = time.perf_counter()
        try:
            result = await self._adapter.invoke(agent, invocation)
        except Exception as error:
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            component, failure_stage, error_type, detail = _error_diagnostics(
                error, self._engine_name
            )
            display_detail = _bounded_single_line(detail)
            failure_fields = {
                **common_fields,
                "component": component,
                "operation": f"{agent}.invoke",
                "failure_stage": failure_stage,
                "error_type": error_type,
                "adapter_error_type": type(error).__name__,
                "error_detail": detail,
                "status": "failed",
                "duration_ms": duration_ms,
            }
            upstream_status_code = getattr(error, "upstream_status_code", None)
            if upstream_status_code is not None:
                failure_fields["upstream_status_code"] = upstream_status_code
            upstream_response = getattr(error, "upstream_response", None)
            response_detail = (
                ""
                if upstream_response is None
                else (
                    ". Response - "
                    f"{self._logger.format_json(upstream_response)}"
                )
            )
            self._logger.error(
                f"{agent.capitalize()} invocation via {component} failed. "
                f"Detail - {error_type}: {display_detail}{response_detail}",
                event="be.agent.invocation.failed",
                source="agent_engine",
                fields=failure_fields,
                response=upstream_response,
            )
            raise
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        response_fields = {
            **result.metadata,
            **common_fields,
            "status": "success",
            "duration_ms": duration_ms,
            "raw_output_chars": len(result.raw_output),
        }
        return AgentInvocationResult(
            raw_output=result.raw_output,
            metadata=response_fields,
        )


def _quoted_message(message: str | None) -> str:
    return json.dumps(_bounded_single_line(message or "", 1_024), ensure_ascii=False)


def _display_engine_name(engine_name: str) -> str:
    return "LangGraph" if engine_name == "langgraph" else engine_name


def _bounded_single_line(value: str, max_characters: int = 512) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_characters:
        return compact
    return f"{compact[: max_characters - 14]}...[TRUNCATED]"


def _error_diagnostics(
    error: Exception, engine_name: str
) -> tuple[str, str, str, str]:
    if isinstance(error, AgentAdapterError):
        component = (
            engine_name if error.component == "agent_engine" else error.component
        )
        return (
            component,
            error.failure_stage,
            error.error_type,
            redact_error_detail(error.detail),
        )
    detail = redact_error_detail(
        str(error).strip() or "unexpected engine failure"
    )
    return engine_name, "invocation", type(error).__name__, detail


def _build_invocation(
    release: PromptRelease,
    output_model: type[BaseModel],
    trip_state: dict[str, Any],
    message: str | None,
    generation: GenerationConfig,
) -> AgentInvocation:
    output_schema = output_model.model_json_schema()
    schema_json = json.dumps(
        output_schema, ensure_ascii=False, separators=(",", ":")
    )
    return AgentInvocation(
        system_prompt=(
            f"{release.content}{OUTPUT_CONTRACT_INSTRUCTION}{schema_json}"
        ),
        user_prompt=frame_untrusted_payload(trip_state, message),
        generation=generation,
    )


_MARKDOWN_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n(?P<body>.*?)\n```", re.DOTALL
)


def _decode_agent_json(raw_output: str) -> Any:
    # Layered fallback for LLM formatting deviations the prompt forbids but
    # cannot fully prevent: try the raw string first, then a fenced block
    # found anywhere in it, then the outermost {...} span as a last resort.
    try:
        return json.loads(raw_output)
    except (TypeError, json.JSONDecodeError):
        pass

    fence_match = _MARKDOWN_FENCE_RE.search(raw_output)
    if fence_match:
        try:
            return json.loads(fence_match.group("body"))
        except json.JSONDecodeError:
            pass

    start, end = raw_output.find("{"), raw_output.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw_output[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Unable to decode agent output", raw_output, 0)


def _parse_and_validate(
    agent: AgentName, raw_output: str, definition: AgentDefinition
) -> tuple[dict[str, Any], list[str]]:
    try:
        decoded = _decode_agent_json(raw_output)
    except (TypeError, json.JSONDecodeError):
        raise _OutputValidationFailure(
            [{"type": "json_invalid", "loc": []}]
        ) from None

    applied_normalizations = normalize_agent_output(agent, decoded)

    try:
        parsed = definition.output_model.model_validate(decoded)
        return parsed.model_dump(mode="json", exclude_none=True), applied_normalizations
    except ValidationError as error:
        failures = _sanitized_validation_failures(error, definition.output_model)
        raise _OutputValidationFailure(failures) from None


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
